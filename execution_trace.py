"""Public execution evidence from Pi, paired by toolCallId, with credential redaction."""
from datetime import datetime
import difflib
import json
import re

from general_rsi.backend import call_diagnostic, text_content


def safe_text(value, limit=16000):
    value = str(value)
    value = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)',
                   '[已隐藏私钥]', value, flags=re.S)
    value = re.sub(r'(?i)(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+[^\s\x22\x27]+)', '[已隐藏凭据]', value)
    value = re.sub(r'''(?ix)((?:["']?)(?:[\w-]*(?:api[_-]?key|password|passwd|secret|token|authorization))["']?\s*[=:]\s*)("[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''',
                   r'\1[已隐藏]', value)
    return value if len(value) <= limit else value[:limit] + '\n…[内容过长，已截断]'


def records(path):
    if not path.exists():
        return
    with path.open(errors='replace') as stream:
        for line in stream:
            try:
                entry = json.loads(line)
                if isinstance(entry, dict):yield entry
            except ValueError:
                continue  # A live append may be incomplete.


def stamp(value, default):
    try:
        if isinstance(value, str):return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        if isinstance(value, (int, float)):return value / 1000 if value > 1e11 else value
    except (ValueError, TypeError):
        pass
    return default


def file_change(name, args, details):
    path = str(args.get('path', args.get('file_path', '')))
    diff = details.get('diff') if isinstance(details, dict) else None
    if not isinstance(diff, str):
        diff = ''
    if not diff and name == 'edit':
        old, new = args.get('oldText', args.get('old_string')), args.get('newText', args.get('new_string'))
        if isinstance(old, str) and isinstance(new, str):
            diff = ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                               fromfile=path, tofile=path))
    if not diff and name == 'write' and isinstance(args.get('content'), str):
        # This is the requested write, not a fabricated before/after diff.
        diff = args['content']
    return path, safe_text(diff, 24000)


def call_trace(folder, receipt, running, created_at):
    """Never serialize raw messages or thinking. Tool IDs distinguish repeated commands."""
    events, tools = {}, {}
    for index, entry in enumerate(records(folder/'session.jsonl')):
        message = entry.get('message', {})
        when = stamp(message.get('timestamp') or entry.get('timestamp'), created_at)
        if message.get('role') == 'assistant':
            content = message.get('content', [])
            if not isinstance(content, list):content = [{'type': 'text', 'text': content}]
            for n, part in enumerate(content):
                if not isinstance(part, dict):continue
                if part.get('type') == 'toolCall':
                    identity = part.get('id') or f'entry-{index}-{n}'
                    tools[identity] = {'name': part.get('name', 'tool'), 'args': part.get('arguments'), 'time': when}
                elif part.get('type') == 'text' and part.get('text', '').strip():
                    identity = f'm-{folder.name}-{entry.get("id", index)}-{n}'
                    events[identity] = {'id': identity, 'time': when, 'type': 'assistant',
                                        'text': safe_text(part['text'], 12000), 'call_id': folder.name}
        elif message.get('role') == 'toolResult':
            identity = message.get('toolCallId')
            if not identity:continue
            tool = tools.setdefault(identity, {'name': message.get('toolName', 'tool'), 'args': {}, 'time': when})
            tool.update(result=message, finished_at=when, is_error=bool(message.get('isError')))

    # Completed sessions are authoritative. The stream adds in-flight output only.
    if running and receipt is None:
        for entry in records(folder/'stdout.log'):
            identity = entry.get('toolCallId')
            kind = entry.get('type')
            if not identity or kind not in ('tool_execution_start', 'tool_execution_update', 'tool_execution_end'):continue
            tool = tools.setdefault(identity, {'name': entry.get('toolName', 'tool'), 'args': entry.get('args', {}),
                                                'time': stamp(entry.get('timestamp'), created_at)})
            if not tool.get('args') and entry.get('args'):tool['args'] = entry['args']
            if 'result' in tool:continue
            if kind == 'tool_execution_end':
                tool.update(result=entry.get('result', {}), is_error=bool(entry.get('isError')))
            elif kind == 'tool_execution_update':tool['partial'] = entry.get('partialResult', {})

    for identity, tool in tools.items():
        args = tool.get('args') or {}
        if not isinstance(args, dict):args = {}
        output = tool.get('result', tool.get('partial', {}))
        if not isinstance(output, dict):output = {}
        name = tool['name']
        path, change = file_change(name, args, output.get('details'))
        command = args.get('command', args.get('cmd', '')) if name in ('bash', 'exec', 'exec_command') else ''
        complete = 'result' in tool
        state = ('error' if tool.get('is_error') else 'completed') if complete else ('running' if running else 'incomplete')
        label = {'bash': '执行命令', 'read': '读取文件', 'write': '写入文件', 'edit': '修改文件'}.get(name, '调用工具 ' + name)
        event_id = f't-{folder.name}-{identity}'
        events[event_id] = {'id': event_id, 'time': tool['time'], 'type': 'tool', 'tool': name,
                            'call_id': folder.name, 'tool_call_id': identity,
                            'text': f'{folder.name} · {label}' + ('：' + safe_text(path, 250) if path else ''),
                            'command': safe_text(command, 16000), 'path': safe_text(path, 2000),
                            'output': safe_text(text_content(output.get('content')), 24000),
                            'change': change, 'change_kind': 'write_content' if name == 'write' else 'diff',
                            'status': state, 'finished_at': tool.get('finished_at')}
    diagnostic = call_diagnostic(folder, receipt) if receipt is not None else None
    if diagnostic:diagnostic = {**diagnostic, 'message': safe_text(diagnostic['message'], 2000)}
    return list(events.values()), diagnostic
