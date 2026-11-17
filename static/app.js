'use strict';
const $=id=>document.getElementById(id);
let selected=null, current=null, refreshing=false, busy=false, lastSignature='', health=null;
const labels={queued:'排队中',launching:'启动中',running:'运行中',stopping:'正在停止',solved:'验收通过',interrupted:'已停止，可恢复',cancelled:'已取消',budget_exhausted:'资源预算已用完',execution_error:'执行异常',protocol_error:'验收协议待处理',integrity_error:'完整性检查未通过',launch_uncertain:'启动状态待核对',ready:'准备中'};
const roles={bootstrap:'主 Agent · 验收规划',research:'研究 Agent',planner:'主 Agent · 方法修订',worker:'实验 Agent'};
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,7000);}
async function api(path,options={}){const response=await fetch(path,{...options,headers:{'X-RSI-Client':'web',...options.headers}});const data=await response.json();if(!response.ok)throw Error(data.error||`请求失败 (${response.status})`);return data;}
function showRun(){for(const id of ['composer','examples'])$(id).hidden=true;$('run').hidden=false;$('heading').textContent='每一次反馈，都有下一步。';$('subtitle').textContent='跟踪真实执行，查看验收结果与留下的方法。';$('new').classList.remove('active');}
function newTask(){const url=new URL(location.href);url.searchParams.delete('job');url.hash='';window.history.replaceState(null,'',url);selected=null;current=null;lastSignature='';$('run').hidden=true;$('composer').hidden=false;$('examples').hidden=false;$('heading').textContent='从一个问题，到可验证的结果。';$('subtitle').textContent='提出方案、执行实验、读取反馈，再把有效方法留下来。';$('new').classList.add('active');$('replay').classList.remove('active');$('question').focus();refreshHistory();}
async function refreshHistory(){try{const history=await api('/api/jobs');$('history').replaceChildren();if(!history.length)$('history').append(node('p','还没有现场任务','muted'));for(const job of history){const b=node('button',undefined,job.id===selected?'selected':'');b.append(node('strong',job.question),node('small',`${labels[job.status]||job.status}${job.queue_position?' · 队列第 '+job.queue_position+' 位':''}${job.limits?.max_seconds?' · 最多 '+(job.limits.max_seconds/3600)+' 小时':''} · ${new Date(job.created_at*1000).toLocaleDateString('zh-CN')}`));b.onclick=()=>selectJob(job.id);$('history').append(b);}}catch(error){$('connection').textContent='连接暂时中断';}}
async function selectJob(id){const url=new URL(location.href);url.searchParams.set('job',id);window.history.replaceState(null,'',url);selected=id;lastSignature='';showRun();$('replay').classList.remove('active');await refreshCurrent();refreshHistory();}
function metrics(calls,checks,rounds,memory){$('metric-calls').textContent=calls;$('metric-checks').textContent=checks;$('metric-rounds').textContent=rounds;$('metric-memory').textContent=memory;}
function timeline(events){
 const sig=JSON.stringify(events);if(sig===lastSignature)return;lastSignature=sig;
 const list=$('timeline'),atEnd=list.scrollTop+list.clientHeight>=list.scrollHeight-70;
 const existing=new Map([...list.children].map(item=>[item.dataset.eventId,item]));
 const fragment=document.createDocumentFragment();
 for(const e of events){
  const encoded=JSON.stringify(e);let item=existing.get(String(e.id));
  if(!item||item.dataset.signature!==encoded){
   const wasOpen=item?.querySelector('details')?.open;
   item=node('li',undefined,e.type==='tool'?'tool':e.diagnostic||e.status==='error'?'failed':e.type==='goal_verified'||e.type==='experience_retained'||e.passed?'good':'');
   item.dataset.eventId=e.id;item.dataset.signature=encoded;if(e.call_id)item.dataset.callId=e.call_id;
   item.append(node('time',new Date(e.time*1000).toLocaleTimeString('zh-CN',{hour12:false})));
   const body=node('div',undefined,'trace-body');
   if(e.type==='tool'){
    const details=node('details',undefined,'trace-detail');details.open=wasOpen??true;
    const summary=node('summary');
    const title=e.command?'Ran '+e.command.split('\n')[0]:e.text;
    summary.append(node('span',title,'trace-title'),node('small',({running:'执行中',completed:'已完成',error:'执行出错',incomplete:'未完成'})[e.status]||'','trace-state '+e.status));
    details.append(summary);
    if(e.command)details.append(node('pre',e.command,'trace-command'));
    if(e.output)details.append(node('pre',e.output,'trace-output'));
    else details.append(node('p',e.status==='running'?'等待工具输出…':e.status==='incomplete'?'未收到工具完成记录':'（无文本输出）','small muted'));
    if(e.change){details.append(node('small',e.change_kind==='write_content'?'提交写入的内容':'文件修改差异','change-label'),node('pre',e.change,'trace-diff'));}
    body.append(details);
   }else body.append(node('span',e.text,e.type==='assistant'?'agent-progress':''));
   item.append(body);
  }
  fragment.append(item);
 }
 list.replaceChildren(fragment);$('event-count').textContent=`${events.length} 条记录`;$('timeline-empty').hidden=events.length>0;
 if(atEnd)list.scrollTop=list.scrollHeight;
}
function agentList(calls){$('agents').replaceChildren();if(!calls.length)$('agents').append(node('p','等待调度','muted'));for(const call of calls){const a=node('div',undefined,'agent'),name=node('div',roles[call.role]||'实验 Agent','agent-name');name.append(node('small',call.id));if(call.diagnostic){a.title=call.diagnostic.message;name.append(node('small',call.diagnostic.message,'agent-error'));}a.append(node('span',(call.role||'A').slice(0,1).toUpperCase(),'agent-avatar'),name,node('span',call.status==='running'?'执行中':call.ok?'已完成':'已停止','agent-state'+(call.status==='running'?' live':'')));$('agents').append(a);}}
let versionJob=null,selectedVersion='baseline',versionEvidence=null;
const versionStates={verified:'已通过复测',failed:'已验收 · 仍有待修订项',pending:'执行或复测中',retained:'已验证并保留',rejected:'未达到改进门槛',not_retained:'未采纳',testing:'候选待验证',execution_incomplete:'运行已结束 · 未完成验证',historical_baseline:'初始三次测量',text_rejected:'文本审查未通过 · 未执行'};
const scoreText=v=>typeof v==='number'?v.toFixed(4):'—';
function diffBlock(text){const pre=node('pre',undefined,'trace-diff');for(const line of text.split('\n')){const span=node('span',line+'\n',line.startsWith('+')&&!line.startsWith('+++')?'diff-add':line.startsWith('-')&&!line.startsWith('---')?'diff-remove':'');pre.append(span);}return pre;}
function feedbackView(attempt){
 const box=node('div',undefined,'feedback-view');if(!attempt)return box;
 box.append(node('p',attempt.feedback_summary,'feedback-headline'));
 for(const c of attempt.components||[]){const row=node('div',undefined,'component-row'),meter=node('meter');meter.min=0;meter.max=1;meter.value=c.score;meter.setAttribute('aria-label',c.label);row.append(node('span',c.label),meter,node('strong',scoreText(c.score)));box.append(row);}
 if(attempt.failed_items?.length){const issues=node('div',undefined,'failed-items');issues.append(node('strong','待修订检查项'));for(const item of attempt.failed_items)issues.append(node('span',`${item.label}${typeof item.score==='number'?' · '+scoreText(item.score):''}`));box.append(issues);}
 return box;
}
function renderProgress(e){
 $('evolution-overview').replaceChildren();for(const [label,value] of [['A 版初始得分',e.baseline&&!e.baseline.valid?'无有效验收':scoreText(e.baseline_score)],['已测最高分',scoreText(e.best_score)],['方法修订',String(e.revision_count)],['复测 / 保留',`${e.validation_runs} / ${e.confirmed_improvements}`]]){const card=node('div');card.append(node('small',label),node('strong',value));$('evolution-overview').append(card);}
 $('evolution-activity').textContent=e.current_activity||'';$('attempt-chart').replaceChildren();
 for(const a of e.attempts){const b=node('button',undefined,'attempt-score '+(a.passed?'passed':'partial'));b.title=a.id+'：'+(a.feedback_summary||'');const track=node('span',undefined,'score-track'),fill=node('span',undefined,'score-fill');fill.style.width=`${Math.max(0,Math.min(1,a.score||0))*100}%`;track.append(fill);b.append(node('span',`版本 ${a.version_label} · ${a.stage==='validation'?'复测':a.stage==='exploration'?'候选执行':'初始执行'}`),track,node('strong',a.valid?scoreText(a.score):'未评分'));b.onclick=()=>{inspectVersion(a.experience_id);focusAttempt(a.id);};$('attempt-chart').append(b);}
 if(!e.attempts.length)$('attempt-chart').append(node('p','等待首次环境验收。','small muted'));
}
function focusAttempt(id){const target=[...$('evolution-checks').querySelectorAll('details')].find(d=>d.dataset.evidenceId===id);if(target){$('evolution-attempts').open=true;target.open=true;target.scrollIntoView({behavior:'smooth',block:'center'});return;}const event=[...$('timeline').children].find(n=>n.dataset.callId===id);if(event){event.scrollIntoView({behavior:'smooth',block:'center'});return;}toast('依据记录：'+id+'。当前列表未包含该记录，可下载完整执行记录查看。');}
function inspectVersion(id){
 const e=versionEvidence,version=e?.versions?.find(v=>v.id===id);if(!version)return;selectedVersion=id;
 for(const button of $('version-columns').querySelectorAll('button')){const active=button.dataset.versionId===id;button.classList.toggle('selected',active);button.setAttribute('aria-pressed',String(active));}
 const box=$('version-inspector');box.replaceChildren();
 box.append(node('h4',`版本 ${version.label}${version.parent_label?' ← '+version.parent_label:''} · ${versionStates[version.status]||version.status}`));
 if(version.kind==='existing_artifact')box.append(node('p','A 是预置初稿参照；没有测量模型初始方法的表现。'));
 box.append(feedbackView(version.result));
 if(version.validation_results?.length){const repeats=node('div',undefined,'validation-chips');repeats.append(node('span','新工作区复测：'));for(const r of version.validation_results){const b=node('button',`${scoreText(r.score)} · ${r.passed?'通过':r.valid?'待修订':'无有效验收'}`,'secondary');b.onclick=()=>focusAttempt(r.id);repeats.append(b);}box.append(repeats);}
 if(version.parent_id&&version.observation)box.append(node('p','修改依据 / 已观察反馈：'+version.observation));
 if(version.hypothesis)box.append(node('p','修改假设：'+version.hypothesis));
 if(version.evidence_refs.length){const refs=node('div',undefined,'version-refs');refs.append(node('span','从这些记录触发修改：'));for(const ref of version.evidence_refs){const button=node('button',ref,'secondary');button.onclick=()=>focusAttempt(ref);refs.append(button);}box.append(refs);}
 if(version.diff){const details=node('details',undefined,'version-diff');details.open=true;details.append(node('summary',`查看 ${version.parent_label} → ${version.label} 的方法差异（红删绿增）`),diffBlock(version.diff));box.append(details);}
 else box.append(node('pre',version.experience||'空经验：按原始任务执行。','file-source'));
 const workspace=(current?.file_workspaces||[]).find(g=>g.final&&version.attempt_refs.some(ref=>g.id==='workspace-'+ref))||(current?.file_workspaces||[]).find(g=>g.id==='workspace-'+version.id);
 if(workspace){const files=node('button','查看本版本文件 ↓','secondary');files.onclick=()=>{fileScope=workspace.id;viewFile=null;fileSignature='';fileGeneration++;renderFiles(current);$('files-panel').scrollIntoView({behavior:'smooth',block:'start'});};box.append(files);}
 const attempts=node('div',undefined,'version-refs');attempts.append(node('span','本版本执行 / 复测：'));if(!version.attempt_refs.length)attempts.append(node('small','尚无完成记录'));for(const ref of version.attempt_refs){const button=node('button',ref,'secondary');button.onclick=()=>focusAttempt(ref);attempts.append(button);}box.append(attempts);
 for(const row of $('evolution-results').children)row.classList.toggle('version-match',row.dataset.methodId===id);
}
function drawGraph(prefix,versions){
 const canvas=$(prefix+'-canvas'),svg=$(prefix+'-edges');if(!canvas||!canvas.offsetWidth)return;
 const origin=canvas.getBoundingClientRect(),ns='http://www.w3.org/2000/svg';
 svg.replaceChildren();svg.setAttribute('width',String(canvas.offsetWidth));svg.setAttribute('height',String(canvas.offsetHeight));
 const defs=document.createElementNS(ns,'defs'),marker=document.createElementNS(ns,'marker');marker.id=prefix+'-arrowhead';marker.setAttribute('markerWidth','8');marker.setAttribute('markerHeight','8');marker.setAttribute('refX','7');marker.setAttribute('refY','4');marker.setAttribute('orient','auto');const arrow=document.createElementNS(ns,'path');arrow.setAttribute('d','M0 0 L8 4 L0 8 Z');arrow.setAttribute('fill','#9db1cb');marker.append(arrow);defs.append(marker);svg.append(defs);
 const buttons=[...$(prefix+'-columns').querySelectorAll('button')];
 for(const v of versions||[]){if(!v.parent_id)continue;const parent=buttons.find(b=>b.dataset.versionId===v.parent_id),child=buttons.find(b=>b.dataset.versionId===v.id);if(!parent||!child)continue;const a=parent.getBoundingClientRect(),b=child.getBoundingClientRect(),x1=a.right-origin.left,y1=a.top+a.height/2-origin.top,x2=b.left-origin.left-5,y2=b.top+b.height/2-origin.top;const path=document.createElementNS(ns,'path');path.setAttribute('d',`M${x1},${y1} C${x1+30},${y1} ${x2-30},${y2} ${x2},${y2}`);path.setAttribute('fill','none');path.setAttribute('stroke','#9db1cb');path.setAttribute('stroke-width','2');path.setAttribute('marker-end',`url(#${prefix}-arrowhead)`);svg.append(path);}
}
function drawVersionEdges(){if(!$('evolution-panel').hidden){drawGraph('version',versionEvidence?.versions);drawGraph('historical',historicalEvidence?.versions);}}
function renderVersions(e,job,preferred){
 if(versionJob!==job){versionJob=job;selectedVersion=preferred||'baseline';}versionEvidence=e;
 $('version-note').textContent=e.version_note||'';$('version-columns').replaceChildren();
 const levels=[...new Set((e.versions||[]).map(v=>v.depth))].sort((a,b)=>a-b);
 for(const depth of levels){const column=node('div',undefined,'version-column');for(const v of e.versions.filter(v=>v.depth===depth)){const button=node('button',undefined,'version-node '+v.status);button.dataset.versionId=v.id;button.append(node('strong','版本 '+v.label),node('span',v.parent_label?'源自版本 '+v.parent_label:v.kind==='existing_artifact'?'预置初稿参照':'初始执行方法'),node('small',versionStates[v.status]||v.status));if(v.result)button.append(node('b',v.result.valid?scoreText(v.result.score):'无有效验收','node-score'));button.onclick=()=>inspectVersion(v.id);column.append(button);}$('version-columns').append(column);}
 if(!(e.versions||[]).some(v=>v.id===selectedVersion))selectedVersion='baseline';inspectVersion(selectedVersion);requestAnimationFrame(drawVersionEdges);
}
window.addEventListener('resize',()=>requestAnimationFrame(drawVersionEdges));
let historicalSignature='',historicalEvidence=null,selectedHistorical='baseline';
function inspectHistorical(id){
 const v=historicalEvidence?.versions?.find(v=>v.id===id);if(!v)return;selectedHistorical=id;
 for(const button of $('historical-columns').querySelectorAll('button')){button.classList.toggle('selected',button.dataset.versionId===id);button.setAttribute('aria-pressed',String(button.dataset.versionId===id));}
 const box=$('historical-inspector');box.replaceChildren();box.append(node('h4',`历史版本 ${v.label} · ${v.id}${v.parent_label?' ← '+v.parent_label:''} · ${versionStates[v.status]||v.status}`));
 const scores=node('div',undefined,'validation-chips');scores.append(node('span','执行：'+(v.scores.map(scoreText).join(' / ')||'未执行')),node('span','复测：'+(v.validation_scores.map(scoreText).join(' / ')||'无记录')));box.append(scores);
 if(v.status==='text_rejected')box.append(node('p','这条修订建议未通过文本审查，没有执行分数或已执行方法差异。它计入 5 次修订记录，不计入 4 次候选执行。'));
 if(v.changes?.length){box.append(node('h4','本版本修改了什么'));for(const change of v.changes){if(typeof change==='string')box.append(node('p','• '+change));else{const item=node('div',undefined,'historical-change');item.append(node('p',change.change||'方法修订'));if(change.targets)item.append(node('p','针对的反馈：'+change.targets));if(change.observable_check)item.append(node('p','如何验证：'+change.observable_check));box.append(item);}}}
 if(v.diff){const diff=node('details',undefined,'version-diff');diff.open=true;diff.append(node('summary',`历史 ${v.parent_label} → ${v.label} 的实际方法差异（红删绿增）`),diffBlock(v.diff));box.append(diff);}
 if(!v.parent_id)box.append(node('p','初始方法未注入修订经验，三次平均 '+scoreText(historicalEvidence.initial_mean)+'。'));
 if(v.gate){box.append(node('p','历史门槛记录：'+(v.gate.accepted?'该轮复测达到当时的保留门槛':'该轮未达到当时的保留门槛')+'；'+(v.gate.reasons||[]).join('、')+'。最终保留以原始记录 selected_candidate 为准。'));}
 const reasons=node('details',undefined,'evidence-check');reasons.append(node('summary','查看触发修订的反馈、假设与审查记录'));
 for(const [title,text] of [['修订原因',v.reason],['已观察反馈',v.observation],['原因假设',v.hypothesis],['拟采取的方法',v.proposed_action]])if(text)reasons.append(node('p',title+'：'+text));
 if(v.review)reasons.append(node('pre',JSON.stringify(v.review,null,2),'file-source'));box.append(reasons);
}
function renderHistorical(data){
 const h=data.benchmark?.historical_reference;$('historical-reference').hidden=!h;historicalEvidence=h;if(!h)return;
 const signature=JSON.stringify(h);if(signature===historicalSignature){requestAnimationFrame(drawVersionEdges);return;}historicalSignature=signature;selectedHistorical='baseline';
 $('historical-summary').textContent=`${h.server} 服务器：${h.method_revision_records} 次修订记录 · ${h.candidates.length} 次候选执行 · 初始平均 ${scoreText(h.initial_mean)} → 最终复测平均 ${scoreText(h.final_mean)}。`;
 $('historical-revision-note').textContent=h.revision_note||h.evidence_error||'当前仅有历史分数摘要，未提供方法差异证据。';
 $('historical-columns').replaceChildren();const versions=h.versions||[];
 for(const depth of [...new Set(versions.map(v=>v.depth))].sort((a,b)=>a-b)){const column=node('div',undefined,'version-column');for(const v of versions.filter(v=>v.depth===depth)){const button=node('button',undefined,'version-node '+v.status);button.dataset.versionId=v.id;button.append(node('strong','历史版本 '+v.label+' · '+v.id),node('span',v.parent_label?'源自历史版本 '+v.parent_label:'初始方法'),node('small',versionStates[v.status]||v.status),node('b',v.scores.length?v.scores.map(scoreText).join(' / '):'未执行','node-score'));button.onclick=()=>inspectHistorical(v.id);column.append(button);}$('historical-columns').append(column);}
 $('historical-inspector').replaceChildren();if(versions.length)inspectHistorical('baseline');requestAnimationFrame(drawVersionEdges);
 $('historical-results').replaceChildren();const first=node('tr');for(const value of ['初始方法','—',h.initial_scores.map(scoreText).join('、'),'—'])first.append(node('td',value));$('historical-results').append(first);
 for(const c of h.candidates){const row=node('tr',undefined,c.id===h.selected_candidate?'historical-selected':'');for(const value of [c.id+(c.id===h.selected_candidate?' · 最终保留':''),c.parent,c.scores.map(scoreText).join('、')||'—',c.validation_scores.map(scoreText).join('、')||'—'])row.append(node('td',value));$('historical-results').append(row);}
 $('historical-scope').textContent=`历史运行：${h.run_id}。这些分数来自当时的运行与 grader。本次从空经验重跑，测量单独显示在下方。历史文件 SHA-256：${h.source_sha256}${h.source_verified?'（原文已核对）':''}`;
}
let evolutionSignature='';
function renderEvolution(data){
 const e=data.evolution;$('evolution-panel').hidden=!e;if(!e)return;if(data.queue_batch&&!e.retention_policy)e.retention_policy_note='本题将采用 baseline 底线规则，运行开始后从 A 版建立比较起点。';renderHistorical(data);
 $('evolution-download').href=data.id==='replay'?'/api/replay':`/api/jobs/${data.id}/evolution`;
 const signature=JSON.stringify([data.id,e]);if(signature===evolutionSignature)return;evolutionSignature=signature;const openSections=new Set([...$('evolution-panel').querySelectorAll('details[open]')].map(d=>d.dataset.evidenceId).filter(Boolean));
 renderProgress(e);$('evolution-title').textContent=e.title;$('evolution-title').className=e.code==='validated_improvement'?'improved':'neutral';
 $('evolution-description').textContent=e.description;
 $('evolution-context').textContent=`${e.mechanism} 基础模型：${e.model||'按任务配置'}。方法候选 ${e.revision_count} 个 · 复测 ${e.validation_runs} 次 · 已保留方法 ${e.confirmed_improvements} 份。${e.baseline_note} ${e.retention_policy_note||''}${e.retention_review?' 本次已按新规则复核已有记录，未重新调用模型。原始执行和旧门槛记录保留在下载证据中。':''}`;
 $('evolution-limitation').textContent=`${e.limitation} 调用失败重试和 JSON 修复不计作方法修订。`;
 $('evolution-steps').replaceChildren();for(const step of e.steps){const item=node('li',undefined,step.done?'done':'pending');item.append(node('strong',step.label),node('small',step.done?'已有记录':e.code==='initial_verified'?'本次未发生':'尚无记录'));$('evolution-steps').append(item);}
 $('evolution-results').replaceChildren();$('evolution-checks').replaceChildren();
 const stageLabels={baseline:'初始执行',existing_artifact_baseline:'预置初稿',exploration:'候选执行',validation:'新工作区复测'};
 const attempts=[...e.attempts].sort((a,b)=>(a.id==='baseline'?-1:b.id==='baseline'?1:0));
 for(const attempt of attempts){const row=node('tr');row.dataset.methodId=attempt.experience_id||'baseline';for(const text of [`${stageLabels[attempt.stage]||attempt.stage} · ${attempt.id}`,`版本 ${attempt.version_label||attempt.experience_id||'A'}`,attempt.valid?(attempt.passed?'通过':'未通过'):'无有效验收',typeof attempt.score==='number'?attempt.score.toFixed(3):'—',typeof attempt.tokens==='number'?attempt.tokens.toLocaleString():'未知'])row.append(node('td',text));$('evolution-results').append(row);
  const detail=node('details',undefined,'evidence-check'),summary=node('summary',attempt.id+' · 具体检查与反馈');detail.dataset.evidenceId=attempt.id;detail.open=openSections.has(attempt.id);detail.append(summary,feedbackView(attempt));
  for(const check of attempt.checks)detail.append(node('p',`${check.passed?'✓':'×'} ${check.id}：${check.evidence}`));
  if(attempt.diagnostic){const raw=node('details',undefined,'evidence-check');raw.append(node('summary','原始验收输出'),node('pre',attempt.diagnostic,'file-source'));detail.append(raw);}$('evolution-checks').append(detail);
 }
 $('evolution-revisions').replaceChildren();
 for(const revision of e.revisions){
  const box=node('details',undefined,'evidence-detail revision-evidence');box.dataset.evidenceId=revision.id;box.open=openSections.has(revision.id);
  box.append(node('summary',`第 ${revision.round} 轮 · 版本 ${e.versions?.find(v=>v.id===revision.id)?.parent_label||revision.parent_id} → ${e.versions?.find(v=>v.id===revision.id)?.label||revision.id} · ${{retained:'已验证并保留',rejected:'未达到采纳门槛',not_retained:'未保留',testing:'待验证',execution_incomplete:'运行已结束 · 未完成验证'}[revision.status]||revision.status}`));
  box.append(node('p','观察到的反馈：'+revision.observation),node('p','改进假设（待复测验证）：'+revision.hypothesis),node('p','依据记录：'+revision.evidence_refs.join('、')));
  const comparison=node('div',undefined,'method-comparison');
  for(const [title,text] of [['原执行方法',revision.before||'空经验：按原始任务直接执行'],['修订后的执行方法',revision.after]]){const block=node('div');block.append(node('strong',title),node('pre',text,'file-source'));comparison.append(block);}box.append(comparison);
  if(revision.diff){const diff=node('details',undefined,'evidence-check');diff.dataset.evidenceId=revision.id+'-diff';diff.open=openSections.has(revision.id+'-diff');diff.append(node('summary','逐行查看方法差异'),node('pre',revision.diff,'trace-diff'));box.append(diff);}
  const gate=revision.measured_gate||revision.gate;
  if(gate){const score=v=>typeof v==='number'?v.toFixed(3):'未知';const tokens=v=>typeof v==='number'?Math.round(v).toLocaleString():'未知';box.append(node('p',`父方法分数 ${score(gate.parent_mean_score)} → 复测平均分 ${score(gate.validation_mean_score)}；父方法平均 token ${tokens(gate.parent_mean_tokens)} → 复测平均 token ${tokens(gate.validation_mean_tokens)}。`));
   const basis=gate.policy==='baseline_floor_v2'?`两次有效复测均不低于本次初始 baseline ${score(gate.baseline_mean_score)}，最低分 ${score(gate.validation_min_score)}；允许从高分回落或打平`:`${gate.quality_gain?'复测平均质量提升':''}${gate.quality_gain&&gate.token_gain?'；':''}${gate.token_gain?'质量不退步且 token 减少':''}`;
   box.append(node('p',revision.accepted?`采纳依据：${basis}；复测记录：${revision.validation_refs.join('、')}。`:'尚无完整的已采纳改进证据。门槛记录：'+(gate.reasons||[]).join('、')));
  }
  if(revision.validation_refs.length)box.append(node('p',`同一方法复测：${revision.same_method?'已核对':'尚未确认'}；同一封存验收：${revision.same_checks?'已核对':'尚未确认'}。`));
  $('evolution-revisions').append(box);
 }
 renderVersions(e,data.id,data.method_experience_id);
}
let retainedJob=null,selectedRetained=null;
function renderOutcome(data){
 $('answer').textContent=data.answer||data.partial_summary||(data.status==='solved'?'任务通过验收，详见产物和报告。':data.reason||'等待任务产出。');
 $('artifacts').replaceChildren();
 for(const file of data.artifacts){const a=node('a',`↓ ${file.partial?'阶段产物 · ':''}${file.name} · ${formatSize(file.size)}`);a.href=file.url;a.download='';$('artifacts').append(a);}
 const methods=data.retained_experiences||[];
 $('method-panel').hidden=!data.method&&!methods.length;
 $('show-methods').hidden=!data.method&&!methods.length;
 $('method-title').textContent=data.method_is_final?'有效方法':`已保留经验（${methods.length}）`;
 $('method-select').hidden=!methods.length;
 $('method-download').hidden=!methods.length;
 $('method-download').href=`/api/jobs/${data.id}/experiences`;
 if(retainedJob!==data.id){retainedJob=data.id;selectedRetained=data.method_experience_id;}
 if(!methods.some(m=>m.id===selectedRetained))selectedRetained=data.method_experience_id;
 $('method-select').replaceChildren();
 for(const method of methods){const option=node('option',`版本 ${method.label} · ${method.id}`);option.value=method.id;option.selected=method.id===selectedRetained;$('method-select').append(option);}
 const show=()=>{
  const method=methods.find(m=>m.id===selectedRetained);
  $('method').textContent=method?.experience||data.method||'';
  const scores=(method?.validation_results||[]).map(r=>r.valid?scoreText(r.score):'无有效验收');
  $('method-note').textContent=method?`同一方法复测：${scores.join(' / ')}；本次 baseline：${scoreText(method.baseline_score)}。保留表示达到复测门槛，任务完成状态见验收结果。`:'经过重复执行验证后保存，供后续任务检索。';
 };
 $('method-select').onchange=()=>{selectedRetained=$('method-select').value;show();};show();
}
$('evolution-example').onclick=async()=>{try{const jobs=await api('/api/jobs');const example=jobs.find(j=>j.validated_experience_count>0);if(example){await selectJob(example.id);$('evolution-panel').scrollIntoView({behavior:'smooth',block:'start'});}else toast('当前历史中还没有已保留新经验的任务。');}catch(error){toast(error.message);}};
let fileJob=null,fileSignature='',fileScope=null,viewFile=null,viewMode='source',fileGeneration=0;
function renderFiles(data){
 $('files-panel').hidden=false;
 const groups=data.file_workspaces||[];
 if(fileJob!==data.id){fileJob=data.id;fileSignature='';fileScope=null;viewFile=null;fileGeneration++;$('file-preview').src='about:blank';$('file-preview').hidden=true;$('file-source').hidden=true;$('file-toolbar').hidden=true;$('file-view-empty').hidden=false;$('file-view-empty').textContent='尚未生成文件。文件写入任务工作区后会自动显示。';}
 const signature=JSON.stringify(groups);if(signature===fileSignature)return;fileSignature=signature;
 if(!groups.some(g=>g.id===fileScope)){fileScope=(groups.find(g=>g.id===data.preferred_workspace)||groups.find(g=>g.final)||[...groups].reverse().find(g=>g.files.length&&g.id!=='workspace-bootstrap')||groups.find(g=>g.files.length)||groups[groups.length-1])?.id;}
 $('file-workspace').replaceChildren();for(const group of groups){const option=node('option',`${group.label} (${group.files.length}${group.truncated?'+':''})`);option.value=group.id;option.selected=group.id===fileScope;$('file-workspace').append(option);}
 const group=groups.find(g=>g.id===fileScope);$('workspace-files').replaceChildren();
 $('download-workspace').hidden=!group?.files.length||group.archive_available===false;if(group)$('download-workspace').href=group.archive_url;
 if(!group?.files.length){fileGeneration++;viewFile=null;$('file-preview').src='about:blank';$('file-preview').hidden=true;$('file-source').hidden=true;$('file-toolbar').hidden=true;$('preview-note').hidden=true;$('file-view-empty').hidden=false;$('file-view-empty').textContent='尚未生成文件。文件写入任务工作区后会自动显示。';$('workspace-files').append(node('p','此分组尚无文件','small muted'));$('files-description').textContent=data.status==='execution_error'?'任务在生成产物前停止；目前没有网页文件。查看执行过程可定位失败原因。':'尚未生成文件。任务写入工作区后会自动显示。';return;}
 $('files-description').textContent=group.final?'当前显示最终产物。':group.verified?'本组产物已通过该任务的验收检查。':'当前为输入文件或实验中间产物；验收状态见文件分组。';
 for(const file of group.files){const row=node('div',undefined,'workspace-file');const button=node('button',file.name,'file-select'+(viewFile?.name===file.name?' selected':''));button.onclick=()=>openFile(file);const download=node('a','↓');download.href=file.download_url;download.download='';download.title='下载 '+file.name;row.append(button,node('small',formatSize(file.size)),download);$('workspace-files').append(row);}
 if(!viewFile||!group.files.some(f=>f.name===viewFile.name)){openFile(group.files.find(f=>/(^|\/)index\.html?$/.test(f.name))||group.files.find(f=>/^(results|output|outputs|artifacts)\/.*\.(md|json|txt)$/.test(f.name))||group.files.find(f=>/(^|\/)(answer|report|summary)\.md$/.test(f.name))||group.files.find(f=>!f.preview_url)||group.files[0]);}
 else{const updated=group.files.find(f=>f.name===viewFile.name);if(updated.modified!==viewFile.modified&&viewMode==='source')openFile(updated,'source');else viewFile=updated;}
}
async function openFile(file,mode){
 const generation=++fileGeneration;viewFile=file;viewMode=mode||(file.preview_url?'preview':'source');
 $('file-toolbar').hidden=false;$('view-file-name').textContent=file.name;$('download-file').href=file.download_url;
 $('view-preview').hidden=!file.preview_url;$('view-source').hidden=!file.source_url;
 $('file-source').hidden=true;$('file-preview').hidden=true;$('preview-note').hidden=true;
 $('reload-preview').hidden=viewMode!=='preview';$('open-preview').hidden=!file.preview_url;if(file.preview_url)$('open-preview').href=file.preview_url;
 $('file-view-empty').hidden=true;
 for(const button of $('workspace-files').querySelectorAll('.file-select'))button.classList.toggle('selected',button.textContent===file.name);
 if(viewMode==='preview'&&file.preview_url){$('file-preview').src=file.preview_url;$('file-preview').hidden=false;$('preview-note').hidden=false;return;}
 $('file-preview').src='about:blank';
 if(!file.source_url){$('file-view-empty').hidden=false;$('file-view-empty').textContent='此格式请下载查看。';return;}
 try{const content=await api(file.source_url);if(generation!==fileGeneration)return;$('file-source').textContent=content.text+(content.truncated?'\n…[文件过长，源码预览已截断，请下载完整文件]':'');$('file-source').hidden=false;}catch(error){if(generation===fileGeneration){$('file-view-empty').hidden=false;$('file-view-empty').textContent=error.message;}}
}
$('show-files').onclick=()=>$('files-panel').scrollIntoView({behavior:'smooth',block:'start'});
$('file-workspace').onchange=()=>{fileScope=$('file-workspace').value;viewFile=null;fileSignature='';fileGeneration++;$('file-preview').src='about:blank';$('file-preview').hidden=true;$('file-source').hidden=true;$('file-toolbar').hidden=true;$('file-view-empty').hidden=false;if(current)renderFiles(current);};
$('view-source').onclick=()=>viewFile&&openFile(viewFile,'source');$('view-preview').onclick=()=>viewFile&&openFile(viewFile,'preview');$('reload-preview').onclick=()=>viewFile&&openFile(viewFile,'preview');
async function refreshCurrent(){if(!selected||selected==='replay'||refreshing)return;refreshing=true;const id=selected;try{const data=await api(`/api/jobs/${id}`);if(selected!==id)return;current=data;showRun();$('benchmark-note').hidden=!data.benchmark;$('benchmark-source').hidden=!data.benchmark?.source_url;if(data.benchmark){$('benchmark-note').textContent=data.benchmark.label+'：'+data.benchmark.note;if(data.benchmark.source_url)$('benchmark-source').href=data.benchmark.source_url;}renderEvolution(data);renderFiles(data);$('mode-label').textContent=data.fixture?'TEST FIXTURE · 无模型调用':'LIVE RUN · 真实执行';$('run-title').textContent=data.question;$('run-meta').textContent=`${new Date(data.created_at*1000).toLocaleString('zh-CN')} · ${data.attachments.length} 个附件 · ${data.limits?.max_seconds?'本题最多 '+(data.limits.max_seconds/3600)+' 小时（从开始执行计时）':'无控制层时间上限'}${data.queue_position?' · 队列第 '+data.queue_position+' 位，前一题结束后自动开始':''}${data.running&&data.deadline?' · 剩余约 '+Math.max(0,Math.ceil((data.deadline-Date.now()/1000)/60))+' 分钟':''}`;$('status').textContent=labels[data.status]||data.status;$('status').className='status '+(data.status==='solved'?'solved':data.running?'running':['queued','launching'].includes(data.status)?'':'error');$('stop').hidden=!(data.running||data.status==='queued');$('resume').hidden=data.running||!['interrupted','execution_error'].includes(data.status);$('notice').hidden=true;if(data.reason&&!data.running&&data.status!=='solved'){ $('notice').hidden=false;$('notice').textContent=data.reason;}if(data.mode==='sample'){ $('notice').hidden=false;$('notice').textContent='现场流程演示：从预置的不合格 result.json 初稿开始，真实 Agent 改进后由计算程序验收。这不是模型改进前后的受控基准对照。';}if(data.fixture){$('notice').hidden=false;$('notice').textContent='脚本 Agent 接口自检，不调用真实模型。';}metrics(data.agent_calls,data.verifier_calls,data.evolution?.revision_count??data.round_index,data.validated_experience_count);timeline(data.events);$('trace-link').hidden=false;$('trace-link').href=`/api/jobs/${id}/trace`;agentList(data.calls);$('authority').textContent=data.verifier_authority==='user_provided'?'验收来源：预置或用户提供的计算检查':data.verifier_authority?'验收来源：Agent 提议的可执行检查；覆盖范围需要人工审视。':'正在确定验收要求。';renderOutcome(data);$('report-link').hidden=!['solved','interrupted','budget_exhausted','execution_error','protocol_error','integrity_error'].includes(data.status);$('report-link').href=`/api/jobs/${id}/report`;$('plans').replaceChildren();for(const p of data.plans)$('plans').append(node('p',p));for(const f of data.feedback){const p=node('p',`${f.attempt}：${f.valid?(f.passed?'通过':'未通过'):'无有效验收'}${f.diagnostic?' · '+f.diagnostic:''}`);$('plans').append(p);}$('plans-panel').hidden=!data.plans.length&&!data.feedback.length;$('feedback-panel').hidden=!data.running;$('connection').textContent='运行时已连接';}catch(error){toast(error.message);}finally{refreshing=false;}}
async function replay(){selected='replay';current=null;lastSignature='';showRun();$('benchmark-note').hidden=true;$('benchmark-source').hidden=true;$('evolution-panel').hidden=true;$('files-panel').hidden=true;$('file-preview').src='about:blank';$('replay').classList.add('active');try{const data=await api('/api/replay');if(selected!=='replay')return;const s=data.summary;renderEvolution({id:'replay',evolution:data.evolution});$('mode-label').textContent='RECORDED RUN · 已完成的真实实测';$('run-title').textContent='GAIA：从 Excel 地块颜色判断能否完成闭合路线';$('run-meta').textContent='官方 2023 validation · Level 1 · 智增增 DeepSeek Flash';$('status').textContent='实测已完成';$('status').className='status solved';$('stop').hidden=true;$('resume').hidden=true;$('notice').hidden=false;$('notice').textContent=data.notice+' 本案例经历初始执行失败、方法修订和两次新工作区复测；单题结果不能代表跨任务提升。';metrics(s.agent_calls,s.verifier_calls,data.evolution?.revision_count??s.revision_rounds,s.validated_experience_count);timeline(data.events);$('trace-link').hidden=true;agentList((s.trace_summary||[]).map(c=>({id:c.call_id,role:c.call_id==='bootstrap'?'bootstrap':c.call_id.startsWith('research')?'research':c.call_id.startsWith('decision')?'planner':'worker',status:'finished',ok:!(c.call_id==='baseline'||c.call_id==='round-0-candidate-1')})));$('authority').textContent='验收来源：官方 GAIA 评分程序；工作区不含参考答案。';$('answer').textContent='最终答案：No\n\n一项方法修订后，候选执行通过官方验收，并在两个新工作区重复通过。实际读取了表格文件和 Skill，保留了分析程序与证据。\n\n原试跑总耗时约 11 分 20 秒；180 秒限制曾导致初始执行和一个候选被中断。当前运行时已按要求取消默认时限。';$('artifacts').replaceChildren();$('report-link').hidden=true;$('show-methods').hidden=true;$('method-panel').hidden=false;$('method-title').textContent='有效方法';$('method-select').hidden=true;$('method-download').hidden=true;$('method-note').textContent='同一方法经过两次新工作区复测后保留。';$('method').textContent='失败观察：长时间搜索没有及时形成可验收产物。\n方法修订：先从原始文件提取可核验事实，优先建立决定性证明，再提交分析脚本、文件证据和最终答案。\n保留依据：候选通过验收，同一方法在两个新工作区执行并再次通过。\n\n这是固定基础模型上的执行方法与经验改进。';$('plans-panel').hidden=true;$('feedback-panel').hidden=true;}catch(error){toast(error.message);}}
function formatSize(n){return n>=1048576?(n/1048576).toFixed(1)+' MiB':n>=1024?(n/1024).toFixed(1)+' KiB':n+' B';}
function fileList(){$('file-list').replaceChildren();for(const id of ['files','skills'])for(const file of $(id).files)$('file-list').append(node('span',`${id==='skills'?'Skill · ':''}${file.name} (${formatSize(file.size)})`,'file-chip'));}
async function start(mode){if(busy)return;busy=true;$('submit').disabled=true;$('sample').disabled=true;try{const form=new FormData();form.append('mode',mode);if(mode==='custom'){form.append('question',$('question').value);let total=0;for(const id of ['files','skills'])for(const file of $(id).files){total+=file.size;form.append(id,file);}if(total>128*1024*1024)throw Error('附件合计超过128MiB');}const job=await api('/api/jobs',{method:'POST',body:form});await selectJob(job.id);toast('任务已进入后台队列，关闭浏览器不会停止执行。');}catch(error){toast(error.message);}finally{busy=false;$('submit').disabled=false;$('sample').disabled=false;}}
$('ask-form').onsubmit=e=>{e.preventDefault();start('custom');};$('sample').onclick=()=>start('sample');$('replay').onclick=replay;$('replay-card').onclick=replay;$('new').onclick=newTask;$('refresh').onclick=refreshHistory;$('files').onchange=fileList;$('skills').onchange=fileList;
for(const action of ['stop','resume'])$(action).onclick=async()=>{if(!selected||selected==='replay')return;$(action).disabled=true;try{await api(`/api/jobs/${selected}/${action}`,{method:'POST',body:'{}'});toast(action==='stop'?'已请求停止，正在保存状态。':'已加入恢复队列。');await refreshCurrent();refreshHistory();}catch(error){toast(error.message);}finally{$(action).disabled=false;}};
$('feedback-form').onsubmit=async e=>{e.preventDefault();if(!selected||selected==='replay')return;try{const result=await api(`/api/jobs/${selected}/feedback`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:$('feedback').value})});toast(result.message);$('feedback').value='';}catch(error){toast(error.message);}};
(async()=>{try{health=await api('/api/health');$('connection').textContent=health.fixture?'脚本测试模式':'运行时已连接';if(health.fixture)toast('当前服务是脚本 Agent 自检模式，不调用模型。');await refreshHistory();const initialJob=new URLSearchParams(location.search).get('job');if(initialJob){await selectJob(initialJob);if(location.hash==='#files')$('files-panel').scrollIntoView({block:'start'});else if(location.hash==='#evolution')$('evolution-panel').scrollIntoView({block:'start'});}}catch(error){$('connection').textContent='连接失败';toast(error.message);}})();
setInterval(()=>{if(document.visibilityState==='visible')refreshCurrent();},1500);setInterval(()=>{if(document.visibilityState==='visible')refreshHistory();},9000);document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'){refreshCurrent();refreshHistory();}});

$('show-methods').onclick=()=>$('method-panel').scrollIntoView({behavior:'smooth',block:'start'});
