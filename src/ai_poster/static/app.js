'use strict';
const $ = (q) => document.querySelector(q);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function setMarkup(selector,html){const node=$(selector);if(node._lastMarkup!==html){node.innerHTML=html;node._lastMarkup=html;}}
const paths={draft:'M4 4h10l4 4v12H4z M13 4v5h5 M8 13h6 M8 17h6',pending:'M12 4a8 8 0 1 1-6 3 M4 3v5h5 M12 8v5l3 2',blocked:'M12 3 2 21h20z M12 9v5 M12 17v1',duplicate:'M8 8h12v12H8z M4 16V4h12',published:'m3 12 6 6L21 6',deleted:'M4 7h16 M9 7V4h6v3 M6 7l1 14h10l1-14 M10 11v6 M14 11v6',sources:'M9 5H5v14h4 M15 5h4v14h-4 M9 12h6 M12 9v6'};
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.draft}" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const sections=[['ready','Черновики','draft'],['pending','В обработке','pending'],['blocked','Требуют внимания','blocked'],['duplicate','Дубли','duplicate'],['published','Опубликовано','published'],['deleted','Удалено','deleted'],['sources','Источники','sources']];
const labels={ready:'Готов к просмотру',pending:'Ожидает обработки',blocked:'Не прошёл проверку',failed:'Ошибка обработки',send_failed:'Не отправлен',uncertain:'Нужно проверить отправку',published:'Опубликован',deleted:'Удалён',skipped:'Пропущен',duplicate:'Повтор',sending:'Отправляется'};
let state={},filter='ready',page=0,search='',selected=null,dirty=false,originalTab=false,loading=false,listSequence=0,detailSequence=0;
let toastTimer,searchTimer;
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,5000);}
async function api(path,method='GET',body){
 const response=await fetch('/api'+path,{method,credentials:'same-origin',headers:method==='GET'?{}:{'Content-Type':'application/json','X-Requested-With':'Tweebit'},...(method==='GET'?{}:{body:JSON.stringify(body??{})})});
 const data=await response.json();
 if(!response.ok){if(response.status===401 && path!='/login')showLogin();throw Error(data.error||'Не удалось выполнить действие.');}return data;
}
function showLogin(){$('#login').hidden=false;$('#app').hidden=true;selected=null;dirty=false;}
function showApp(){$('#login').hidden=true;$('#app').hidden=false;}
function countFor(id){const c=state.counts||{};if(id==='blocked')return(c.blocked||0)+(c.failed||0)+(c.send_failed||0)+(c.uncertain||0);if(id==='deleted')return(c.deleted||0)+(c.skipped||0);if(id==='sources')return(state.sources||[]).length;return c[id]||0;}
function renderState(){
 setMarkup('#navigation',sections.map(([id,label,ic])=>`<button class="nav-item ${filter===id?'active':''} ${id==='sources'?'source-nav':''}" data-action="section" data-section="${id}" ${filter===id?'aria-current="page"':''}>${icon(ic)}<span>${label}</span><span class="badge">${countFor(id)}</span></button>`).join(''));
 $('#channel-name').textContent=state.channel||'Подключить канал';
 $('#run-state').textContent=state.paused?'Сбор на паузе':'Сбор включён';$('#pause-button').textContent=state.paused?'▶ Включить сбор':'Ⅱ Пауза';
 for(const key of ['ready','pending','published','duplicate'])$('#count-'+key).textContent=state.counts?.[key]||0;
 $('#processing-note').textContent=state.processing_id?`Готовим пост #${state.processing_id}…`:state.paused?'Сбор на паузе · можно редактировать черновики':state.mode==='manual'?'Публикация после вашего подтверждения':'Автопубликация включена';
 const title=sections.find(x=>x[0]===filter)?.[1]||'Черновики';$('#page-title').textContent=title;$('#breadcrumb').textContent=title;
 const descriptions={ready:'Последнее слово — за вами. Отредактируйте и отправьте в канал.',pending:'Бот создаёт английские посты и проверяет факты и повторы.',blocked:'Посмотрите причину, повторите обработку или удалите пост.',duplicate:'Эти посты не отправятся повторно в ваш канал.',published:'История публикаций вашего канала.',deleted:'Эти посты больше не попадут в очередь.',sources:'Только те каналы и аккаунты, которые выбрали вы.'};
 $('#page-description').textContent=descriptions[filter];
 $('#posts-workspace').hidden=filter==='sources';$('#sources-page').hidden=filter!=='sources';
 if(filter==='sources')renderSources();
}
async function refreshState(){state=await api('/state');renderState();}
function renderSources(){
 setMarkup('#source-list',(state.sources||[]).map(s=>`<article class="source-card"><span class="source-platform">${s.kind==='telegram'?'TELEGRAM':'X / TWITTER'}</span><h3>@${esc(s.handle)}</h3><p>${s.error?'Ошибка: '+esc(s.error):s.history_since?'Загрузка последних 72 часов ожидает сбора':'Подключён · отслеживаем новые посты'}</p><button class="icon-button" data-action="remove-source" data-id="${s.id}" data-handle="${esc(s.handle)}">${icon('deleted')} Удалить источник</button></article>`).join('')||'<div class="empty-list">Добавьте первый Telegram-канал или X-аккаунт.</div>');
}
async function loadList(autoSelect=false){
 if(filter==='sources')return;
 const sequence=++listSequence;
 const data=await api(`/posts?filter=${encodeURIComponent(filter)}&page=${page}&q=${encodeURIComponent(search)}`);
 if(sequence!==listSequence)return;
 $('#list-total').textContent=`Постов: ${data.total}`;
 setMarkup('#post-list',data.items.map(p=>`<button class="post-card ${selected?.id===p.id?'active':''}" data-action="select" data-id="${p.id}" aria-label="Открыть пост ${p.id}"><div class="post-card-top"><span class="source-handle">${p.kind==='telegram'?'↗':'𝕏'} @${esc(p.handle)}</span><span class="post-number">#${p.id}</span></div><h3>${esc(p.preview.split('\n').filter(Boolean).slice(0,2).join(' '))}</h3><div class="post-card-bottom"><span class="status-pill ${esc(p.state)}">${state.processing_id===p.id?'Готовим пост…':labels[p.state]||p.state}</span><span class="meta">${p.edited_by_owner?'Изменён вами':['ready','published'].includes(p.state)?'EN':''}</span></div></button>`).join('')||'<div class="empty-list">Здесь пока нет постов.<br>Проверьте другой раздел или источники.</div>');
 $('#pagination').innerHTML=data.total>20?`<button data-action="page" data-page="${page-1}" ${page===0?'disabled':''}>←</button><span>${page+1} / ${Math.ceil(data.total/20)}</span><button data-action="page" data-page="${page+1}" ${(page+1)*20>=data.total?'disabled':''}>→</button>`:'';
 if(autoSelect){if(data.items.length)await openPost(data.items[0].id);else{selected=null;renderEditor();}}
}
async function openPost(id){const sequence=++detailSequence;const post=await api('/posts/'+id);if(sequence!==detailSequence)return;selected=post;dirty=false;originalTab=false;renderEditor();document.querySelectorAll('.post-card').forEach(el=>el.classList.toggle('active',Number(el.dataset.id)===id));}
function renderEditor(){
 const el=$('#editor'),p=selected;
 if(!p){el.innerHTML='<div class="empty-editor"><span class="empty-symbol">✎</span><h2>Место для вашего поста</h2><p>Выберите публикацию слева.</p></div>';return;}
 const editable=p.state==='ready',canDelete=!['published','sending','uncertain','deleted'].includes(p.state),retry=['blocked','failed','send_failed'].includes(p.state);
 el.innerHTML=`<div class="editor-top"><div><h2>Пост #${p.id}</h2><small>${esc(labels[p.state]||p.state)}${p.edited_by_owner?' · отредактирован вами':''}</small></div><div class="editor-tools">${canDelete?`<button class="icon-button" data-action="delete">${icon('deleted')} Удалить</button>`:''}</div></div><div class="editor-tabs"><button class="editor-tab ${!originalTab?'active':''}" data-action="draft-tab">Ваш пост <span class="meta">EN</span></button><button class="editor-tab ${originalTab?'active':''}" data-action="original-tab">Оригинал</button></div><div class="editor-body">${p.reason?`<div class="notice">${esc(p.reason)}${p.duplicate_of?`<br><button class="secondary compact" data-action="select" data-id="${p.duplicate_of}">Открыть пост #${p.duplicate_of}</button>`:''}</div>`:''}${originalTab?`<div class="editor-label">Исходный текст</div><div class="original-text">${esc(p.original)}</div>`:p.draft?`<div class="editor-label"><label for="draft-text">${editable?'Текст публикации':'Опубликованный / сохранённый текст'}</label><span id="changed-label" class="changed">${editable?'Можно редактировать':''}</span></div><textarea id="draft-text" aria-label="Текст публикации" spellcheck="false" ${editable?'':'readonly'}>${esc(p.body)}</textarea><div class="editor-footnote"><span>${editable?'Убирайте лишнее и меняйте формулировки прямо здесь.':'Текст доступен только для чтения.'}</span><span id="char-count"></span></div>`:`<div class="empty-editor"><span class="empty-symbol">${p.state==='pending'?'◷':'✎'}</span><h2>${p.state==='pending'?'Пост ещё готовится':'Черновик не создан'}</h2><p>${p.state==='pending'?(state.paused?'Включите сбор, чтобы начать обработку.':'После обработки здесь появится текст на английском.'):'Откройте оригинал и посмотрите причину выше.'}</p></div>`}${originalTab?`<div class="source-footer">Оригинал (не публикуется): <a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">${esc(p.url.replace('https://',''))}</a></div>`:''}</div><div class="editor-actions"><span class="save-note">${state.mode==='manual'?'Публикация по вашему решению':'Включена автопубликация'}</span><div>${retry?'<button class="secondary" data-action="retry">Повторить обработку</button>':''}${editable?`<button id="save-button" class="secondary" data-action="save" disabled>Сохранить</button><button class="primary" data-action="publish" ${state.paused?'disabled title="Сначала включите сбор"':''}>Опубликовать ↗</button>`:''}</div></div>`;
 if($('#draft-text')){$('#draft-text').addEventListener('input',onEdit);updateCount();}
}
function onEdit(){dirty=$('#draft-text').value!==selected.body;$('#save-button').disabled=!dirty;$('#changed-label').textContent=dirty?'Есть несохранённые правки':'Можно редактировать';updateCount();}
function updateCount(){const len=($('#draft-text')?.value||'').length;$('#char-count').textContent=`${len.toLocaleString('ru-RU')} / 4 096`;$('#char-count').classList.toggle('over-limit',len>4096);}
async function save(){const p=await api('/posts/'+selected.id,'PATCH',{text:$('#draft-text').value,version:selected.version});selected=p;dirty=false;renderEditor();await loadList();toast('Правки сохранены.');}
function dialog(html){$('#dialog-content').innerHTML=html;$('#dialog').showModal();}
function closeDialog(){$('#dialog').close();}
function confirmDialog(title,text,button='Продолжить',danger=false){return new Promise(resolve=>{dialog(`<h2>${esc(title)}</h2><p>${esc(text)}</p><div class="dialog-buttons"><button class="secondary" id="cancel-confirm">Отмена</button><button class="${danger?'danger':'primary'}" id="ok-confirm">${esc(button)}</button></div>`);const finish=value=>{closeDialog();$('#dialog').removeEventListener('cancel',cancel);resolve(value);};const cancel=event=>{event.preventDefault();finish(false);};$('#dialog').addEventListener('cancel',cancel);$('#cancel-confirm').onclick=()=>finish(false);$('#ok-confirm').onclick=()=>finish(true);});}
async function leaveEditor(){return !dirty||await confirmDialog('Есть несохранённые правки','Перейти дальше и не сохранять изменения?','Не сохранять',true);}
async function formDialog(type,handle){
 const source=type==='source';
 dialog(`<h2>${source?'Добавить источник':'Канал публикации'}</h2><p>${source?'Укажите публичный канал Telegram или аккаунт X. Загрузим последние 72 часа.':'Бот и вы должны быть администраторами канала.'}</p><form id="settings-form">${source?'<label for="source-kind">Платформа</label><select id="source-kind"><option value="telegram">Telegram</option><option value="x">X / Twitter</option></select>':''}<label for="source-value">${source?'Ссылка или @username':'@username или числовой ID канала'}</label><input id="source-value" required placeholder="${source?'https://t.me/channel':'@my_channel'}" value="${esc(handle||'')}"><div id="form-error" class="dialog-error" role="alert"></div><div class="dialog-buttons"><button type="button" class="secondary" data-action="close-dialog">Отмена</button><button class="primary" type="submit">${source?'Подключить':'Сохранить'}</button></div></form>`);
 $('#settings-form').onsubmit=async event=>{event.preventDefault();const btn=event.submitter;btn.disabled=true;try{const val=$('#source-value').value;const result=await api(source?'/sources':'/channel','POST',source?{kind:$('#source-kind').value,handle:val}:{channel:val});closeDialog();await refreshState();toast(result.message);}catch(e){$('#form-error').textContent=e.message;}finally{btn.disabled=false;}};
}
async function action(button){
 const a=button.dataset.action;
 if(a==='close-dialog'){closeDialog();return;}
 if(a==='section'){if(!await leaveEditor())return;dirty=false;selected=null;filter=button.dataset.section;page=0;search='';$('#search').value='';renderState();renderEditor();await loadList(true);}
 else if(a==='select'){if(await leaveEditor())await openPost(Number(button.dataset.id));}
 else if(a==='page'){if(!await leaveEditor())return;dirty=false;page=Number(button.dataset.page);await loadList(true);}
 else if(a==='save')await save();
 else if(a==='original-tab'||a==='draft-tab'){if(dirty&&!await leaveEditor())return;dirty=false;originalTab=a==='original-tab';renderEditor();}
 else if(a==='delete'){if(!await confirmDialog('Удалить этот пост?','Он исчезнет из очереди и не будет опубликован. Бот запомнит его, чтобы не предлагать снова.','Удалить',true))return;await api('/posts/'+selected.id,'DELETE',{version:selected.version});selected=null;dirty=false;await refreshState();await loadList(true);toast('Пост удалён из очереди.');}
 else if(a==='publish'){if(dirty)await save();if(!await confirmDialog('Опубликовать пост?',`Текст будет отправлен в ${state.channel}.`,'Опубликовать'))return;const result=await api('/posts/'+selected.id+'/publish','POST',{version:selected.version});selected=result.post;renderEditor();await refreshState();await loadList();toast(result.message);}
 else if(a==='retry'){const result=await api('/posts/'+selected.id+'/retry','POST',{version:selected.version});selected=result.post;renderEditor();await refreshState();await loadList();toast(result.message);}
 else if(a==='toggle-pause'||a==='check'){const result=await api('/control','POST',{action:a==='check'?'run':state.paused?'resume':'pause'});await refreshState();if(!dirty)renderEditor();toast(result.message);}
 else if(a==='add-source')await formDialog('source');
 else if(a==='channel')await formDialog('channel',state.channel);
 else if(a==='remove-source'){if(!await confirmDialog('Отключить источник?',`@${button.dataset.handle}: чтение остановится, его незавершённые посты будут пропущены.`,'Отключить',true))return;const result=await api('/sources/'+button.dataset.id,'DELETE');await refreshState();toast(result.message);}
 else if(a==='logout'){if(!await leaveEditor())return;await api('/logout','POST');showLogin();}
}
document.addEventListener('click',async event=>{const button=event.target.closest('[data-action]');if(!button||button.disabled||loading)return;loading=true;try{await action(button);}catch(e){toast(e.message);}finally{loading=false;}});
$('#login-form').addEventListener('submit',async event=>{event.preventDefault();const btn=event.submitter;btn.disabled=true;$('#login-error').textContent='';try{await api('/login','POST',{code:$('#login-code').value});$('#login-code').value='';await refreshState();showApp();await loadList(true);}catch(e){$('#login-error').textContent=e.message;}finally{btn.disabled=false;}});
$('#search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(async()=>{search=$('#search').value;page=0;try{await loadList(false);}catch(e){toast(e.message);}},250);});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
document.addEventListener('keydown',async e=>{if((e.metaKey||e.ctrlKey)&&e.key==='s'&&dirty){e.preventDefault();try{await save();}catch(error){toast(error.message);}}});
(async()=>{try{await refreshState();showApp();await loadList(true);}catch{showLogin();}})();
setInterval(async()=>{if($('#app').hidden||loading||$('#dialog').open)return;try{await refreshState();await loadList(false);if(selected&&!dirty){const p=await api('/posts/'+selected.id);if(p.version!==selected.version){selected=p;renderEditor();}}}catch{}},12000);
