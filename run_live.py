"""Show actual controller events while a real-model demonstration runs.

The optional self-test uses the runtime's clearly labeled deterministic fixture.
This wrapper does not modify the RSI controller or fake its progress/results.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / 'rsi'


def emit(text):
    print(text, flush=True)


def label(identity):
    if identity == 'baseline':
        return '初始方法/产物'
    if '-validation-' in identity:
        return '固定方法复测' + str(int(identity.rsplit('-', 1)[1]) + 1)
    if '-candidate-' in identity:
        return '候选方案' + str(int(identity.rsplit('-', 1)[1]) + 1)
    return identity


def explain(event):
    kind = event['type']
    if kind == 'agent_started':
        role, identity = event['role'], event['call_id']
        if role == 'bootstrap':return '主Agent建立验收要求'
        if role == 'research':return '研究Agent处理信息缺口'
        if role == 'planner':return '主Agent分析反馈，提出下一版方法'
        return label(identity) + '：Agent开始实际执行'
    if kind == 'agent_finished' and not event['ok']:
        return label(event['call_id']) + '：调用未正常完成，保留失败记录'
    if kind == 'agent_output_repair':return '模型回复被输出上限截断，进行一次简短修复；本次修复计入调用预算'
    if kind == 'acceptance_sealed':return '验收标准已确定，后续候选共用这套标准'
    if kind == 'research_recorded':return '研究结果已记录' if not event['failed'] else '本次研究未成功，控制器已记录'
    if kind == 'environment_result':
        who = label(event['attempt_id'])
        if not event['valid']:return who + '：未形成有效验收结果'
        return who + ('：环境验收通过' if event['passed'] else '：环境验收未通过，反馈将用于改进')
    if kind == 'unverified_completion_rejected':return '尚无通过的环境验收，继续处理'
    if kind == 'experience_retained':return '方法通过复测，已写入经验库'
    if kind == 'goal_verified':return '任务已通过环境验收，保存最终结果'
    return None


def show_events(path, seen):
    if not path.is_file():return seen
    lines = path.read_text(errors='replace').splitlines()
    for raw in lines[seen:]:
        try:event = json.loads(raw)
        except ValueError:break  # Leave an incomplete append for the next read.
        seen += 1
        message = explain(event)
        if message:emit('  · ' + message)
    return seen


def main():
    parser = argparse.ArgumentParser(description='显示真实RSI事件；默认演示真实模型改进预置初稿')
    parser.add_argument('--question', help='改为现场输入自己的问题，使用系统默认/提供的验收')
    parser.add_argument('--workspace', help='自己的问题所需的输入目录')
    parser.add_argument('--config', help='可选的完整RSI配置；未提供时所有模型角色为智增增Flash')
    parser.add_argument('--output', help='本次演示新目录；必须尚不存在')
    parser.add_argument('--self-test', action='store_true', help='只用脚本Agent检查流程，不调用真实模型')
    args = parser.parse_args()
    if args.self_test and (args.question or args.workspace or args.config):
        parser.error('--self-test不能与自定义问题、输入或配置一起使用')
    if args.workspace and not args.question:
        parser.error('--workspace用于--question；默认演示使用随包初稿')
    if not (RUNTIME / 'run.sh').is_file():
        parser.error('未找到同目录rsi运行时，请保留完整演示包目录')
    run = (Path(args.output).expanduser().resolve() if args.output else
           Path.home() / '.local/share/general-rsi/demo-runs' /
           (time.strftime('run-%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]))
    run.mkdir(parents=True, exist_ok=False)
    goal = run / 'goal'
    if args.config:
        config = json.loads(Path(args.config).expanduser().read_text())
    elif args.self_test:
        config = json.loads((RUNTIME / 'examples/demo.json').read_text())
        for key in ['max_seconds', 'agent_timeout', 'verifier_timeout']:
            config['limits'][key] = None
    else:
        config = {'backend': {'type': 'pi', 'provider': 'zhizengzeng', 'model': 'deepseek-v4.1-flash',
                              'env_file': '~/.pi/agent/zhizengzeng.env', 'args': ['--offline']}}
        if not args.question:
            config['backend']['args'] += ['--no-extensions', '--no-skills', '--no-context-files']
    (run / 'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2)+'\n')
    if args.question:
        question = args.question
        workspace = Path(args.workspace).expanduser().resolve() if args.workspace else None
    else:
        question = ('按task.txt改进现有result.json，实际计算并提交合法整数x，使目标达到全域最小。'
                    '从原始输入执行，保留可复核计算。已有初稿先由环境验收。')
        workspace = ROOT / 'sample-input'
        config['verifier'] = {'command': [sys.executable, str(RUNTIME / 'examples/demo_verifier.py'), '{workspace}'],
                              'mode': 'json', 'check_ids': ['integer_domain', 'minimum'], 'authority': 'user_provided'}
        (run / 'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2)+'\n')
    command = ['bash', str(RUNTIME/'run.sh'), 'ask', question, '--output', str(goal),
               '--memory', str(run/'memory'), '--config', str(run/'config.json')]
    if workspace:command += ['--workspace', str(workspace)]
    if not args.question:command.append('--baseline-existing')
    emit('RSI 现场演示')
    emit('模式：脚本Agent流程自检，不调用模型' if args.self_test else '模式：真实模型/Agent调用，按环境反馈运行')
    if not args.question:
        emit('样例：预置一份不合格初稿，让系统改进并复测；这是流程演示，不是模型改进前后的基准对照。')
    emit('输出目录：' + str(run))
    emit('控制层时间配置：' + str({key:config.get('limits',{}).get(key) for key in ['max_seconds','agent_timeout','verifier_timeout']}) + '；可用Ctrl-C停止。开始后只显示真实事件。')
    seen = 0
    with (run/'controller.stdout.log').open('w') as out, (run/'controller.stderr.log').open('w') as err:
        process = subprocess.Popen(command, cwd=ROOT, stdout=out, stderr=err, start_new_session=True)
        try:
            while process.poll() is None:
                seen = show_events(goal/'events.jsonl', seen)
                time.sleep(.25)
        except KeyboardInterrupt:
            emit('已请求停止，等待控制器保存状态。')
            if process.poll() is None:process.send_signal(signal.SIGINT)
            process.wait()
        seen = show_events(goal/'events.jsonl', seen)
    report_path = goal/'report.json'
    if not report_path.is_file():
        emit('运行未生成报告，请查看：' + str(run/'controller.stderr.log'))
        return process.returncode or 2
    report = json.loads(report_path.read_text())
    emit('\n最终状态：' + report['status'])
    emit('Agent调用：{}；环境验收：{}；经验修订轮数：{}；新保留经验：{}'.format(
        report['agent_calls'],report['verifier_calls'],report['round_index'],report['validated_experience_count']))
    if report.get('reason'):emit('说明：' + report['reason'])
    if report['status'] == 'solved' and report['round_index'] == 0:
        emit('初始方法已通过复测，本次没有触发新的经验修订。')
    if report.get('answer_attempt'):
        workspace = goal/'workspaces'/report['answer_attempt']
        emit('最终产物：' + str(workspace))
        result = workspace/'result.json'
        if not args.question and result.is_file():emit('计算结果：' + result.read_text().strip())
        answer = goal/'answer.md'
        if answer.is_file():emit('\n最终回答：\n' + answer.read_text().strip())
    state = json.loads((goal/'state.json').read_text())
    if report.get('final_experience') and report['validated_experience_count']:
        method = state['experiences'][report['final_experience']]['experience']
        (run/'retained-method.txt').write_text(method+'\n')
        emit('改进后的方法：' + str(run/'retained-method.txt'))
    emit('验收报告：' + str(report_path))
    emit('经验库：' + str(run/'memory'))
    emit('恢复命令：bash ' + str(RUNTIME/'run.sh') + ' resume ' + str(goal))
    return 0 if report['status']=='solved' else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print('演示未启动/未完成：' + str(error), file=sys.stderr)
        raise SystemExit(2)
