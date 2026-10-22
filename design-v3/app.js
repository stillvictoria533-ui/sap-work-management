
(()=>{
  const root=document.getElementById('sap-desk-design');
  const content=root.querySelector('#sd-content');
  const statusNames={pending:'待受理','in-progress':'处理中',waiting:'待确认',done:'已完成',closed:'已关闭'};
  const priorities={urgent:'紧急',high:'高',medium:'中',low:'低'};
  const transitions={pending:['in-progress','closed'],'in-progress':['waiting','closed'],waiting:['in-progress','done','closed'],done:['in-progress','closed'],closed:['in-progress']};
  const modules=['FI','CO','MM','SD','PP','HR','其他'];
  const types=['配置','开发','报表','接口','问题','其他'];
  const people=['林晓','陈默','周宁','许言'];
  const admin='管理员';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon=name=>`<i data-lucide="${name}" aria-hidden="true"></i>`;
  // Sample accounts exist only in this mockup's memory. No credentials are persisted or transmitted.
  const accounts=[
    {id:1,name:'管理员',email:'admin@sapdesk.example',role:'admin',enabled:true,mustChange:false,password:'Preview2026!'},
    {id:2,name:'林晓',email:'linxiao@sapdesk.example',role:'consultant',enabled:true,mustChange:false,password:'Preview2026!'},
    {id:3,name:'陈默',email:'chenmo@sapdesk.example',role:'consultant',enabled:true,mustChange:false,password:'Preview2026!'},
    {id:4,name:'周宁',email:'zhouning@sapdesk.example',role:'consultant',enabled:true,mustChange:false,password:'Preview2026!'},
    {id:5,name:'许言',email:'xuyan@sapdesk.example',role:'consultant',enabled:true,mustChange:false,password:'Preview2026!'},
    {id:6,name:'赵可',email:'zhaoke@sapdesk.example',role:'consultant',enabled:false,mustChange:false,password:'Preview2026!'}
  ];
  const accountLogs=[{time:'2026-10-08 16:10',text:'管理员停用了赵可的账号，历史工单保留。'}];
  let accountUI={q:'',role:'',status:'',editId:null,confirm:null};
  const current=()=>accounts.find(a=>a.id===state.userId);
  const brand=()=>'<div class="sd-brand"><span class="sd-mark" aria-hidden="true"><b></b><b></b><b></b><b></b></span>SAP Desk</div>';
  const seed=[
    {id:1048,title:'采购订单审批策略调整',module:'MM',type:'配置',priority:'high',status:'pending',owner:'林晓',date:'10-09 10:24',due:'2026-10-16',next:'admin',unread:['admin'],description:'业务背景\n现有采购订单审批未区分采购金额与采购组织，需调整释放策略。\n\n期望结果\n采购金额大于 10 万元时，增加采购总监审批节点；保留现有其他审批规则。\n\n验收标准\n分别验证 10 万元以下、10 万元以上及不同采购组织的审批路径。',files:[{name:'采购审批规则.xlsx',size:'128 KB'}]},
    {id:1047,title:'月末成本结转报表增加明细',module:'CO',type:'报表',priority:'medium',status:'in-progress',owner:'陈默',date:'10-09 09:40',due:'2026-10-20',next:'admin',unread:[],description:'月末核对成本时，需要按成本中心和成本要素查看结转明细。\n\n期望在现有报表增加明细切换与合计行，导出字段与页面保持一致。',files:[]},
    {id:1046,title:'销售订单接口新增交货日期',module:'SD',type:'接口',priority:'high',status:'waiting',owner:'林晓',date:'10-09 09:15',due:'2026-10-14',next:'consultant',unread:['consultant'],description:'业务背景\n外部订单平台需要回传客户要求的交货日期，目前 SAP 接口中尚未提供该字段。\n\n期望结果\n在销售订单创建接口中增加 requested_delivery_date，支持按订单行传入。\n\n验收标准\n日期可正确写入订单行；为空时使用现有默认规则。',files:[{name:'销售订单接口说明.pdf',size:'246 KB'}]},
    {id:1045,title:'应付凭证过账期间校验异常',module:'FI',type:'问题',priority:'urgent',status:'in-progress',owner:'周宁',date:'10-08 16:30',due:'2026-10-10',next:'admin',unread:['admin'],description:'部分应付凭证在开放的会计期间仍提示不允许过账。已补充复现步骤和脱敏截图，请协助定位期间变式与账户类型的校验。',files:[]},
    {id:1044,title:'生产工单批量打印优化',module:'PP',type:'开发',priority:'low',status:'done',owner:'许言',date:'10-08 14:10',due:'2026-10-12',next:'none',unread:[],description:'希望生产工单打印支持按工厂与计划日期批量选择，打印模板保持现有样式。',files:[]},
    {id:1043,title:'员工组织分配字段调整',module:'HR',type:'配置',priority:'medium',status:'closed',owner:'陈默',date:'10-07 15:20',due:'2026-10-08',next:'none',unread:[],description:'在员工主数据维护时增加组织分配的必填校验。已验证并完成交付。',files:[]},
    {id:1042,title:'库存差异清单导出需求',module:'MM',type:'报表',priority:'medium',status:'pending',owner:'许言',date:'10-07 11:00',due:'',next:'admin',unread:['admin'],description:'提供盘点数量与系统库存差异清单，支持按库存地点筛选并导出。',files:[]},
    {id:1041,title:'客户主数据校验规则补充',module:'SD',type:'其他',priority:'low',status:'closed',owner:'林晓',date:'10-06 10:00',due:'2026-10-07',next:'none',unread:[],description:'补充客户主数据税号字段的校验规则，并说明历史数据处理方式。',files:[]}
  ];
  let tickets=seed.map(t=>({...t,ownerId:accounts.find(a=>a.name===t.owner)?.id,events:[{kind:'event',actor:t.owner,time:'2026-10-06 10:00',text:'提交了需求工单'}]}));
  const conversation=tickets.find(t=>t.id===1046);
  conversation.events=[
    {kind:'event',actor:'林晓',time:'2026-10-07 09:30',text:'提交了需求工单'},
    {kind:'event',actor:admin,time:'2026-10-07 10:05',text:'将状态从「待受理」变更为「处理中」'},
    {kind:'reply',actor:admin,time:'2026-10-08 14:20',text:'接口字段已补充，按 YYYY-MM-DD 格式传入。请使用附件中的示例在测试环境验证空值和多行订单场景。'},
    {kind:'reply',actor:'林晓',time:'2026-10-08 16:45',text:'单行订单已验证通过。多行订单的日期可以不同吗？业务侧有拆分交货的情况。'},
    {kind:'reply',actor:admin,time:'2026-10-09 09:15',text:'可以，每行独立处理日期。多行示例也已补充，请再确认这一场景。'},
    {kind:'event',actor:admin,time:'2026-10-09 09:15',text:'将状态从「处理中」变更为「待确认」'}
  ];
  tickets.filter(t=>t.status!=='pending'&&t.id!==1046).forEach(t=>{t.events.push({kind:'reply',actor:admin,time:'2026-'+t.date,text:t.status==='closed'?'需求已交付并确认，本工单归档。':'需求已受理，当前进展已同步，请在此继续补充业务信息。'});t.events.push({kind:'event',actor:admin,time:'2026-'+t.date,text:`将状态更新为「${statusNames[t.status]}」`});});
  let state={userId:null,role:'admin',view:'home',scope:'list',tab:'all',module:'',priority:'',status:'',owner:'',q:'',page:1,extra:false,ticket:1046,exportOpen:false};
  let draft={title:'',module:'MM',type:'配置',priority:'medium',due:'',description:'',files:[]};
  const design={density:'舒展',radius:10,grid:true};
  function visible(){return current()?tickets.filter(t=>state.role==='admin'||t.ownerId===state.userId):[]}
  function needs(t){return t.status!=='closed'&&(t.next===state.role||t.unread.includes(state.role))}
  function filtered(){return visible().filter(t=>{
    if(state.scope==='archive'&&t.status!=='closed')return false;
    if(state.scope==='attention'&&!needs(t))return false;
    if(state.tab==='active'&&['done','closed'].includes(t.status))return false;
    if(state.tab==='unread'&&!t.unread.includes(state.role))return false;
    if(state.module&&t.module!==state.module)return false;
    if(state.priority&&t.priority!==state.priority)return false;
    if(state.status&&t.status!==state.status)return false;
    if(state.owner&&t.owner!==state.owner)return false;
    return !state.q||(`${t.id} ${t.title} ${t.description} ${t.events.map(e=>e.text).join(' ')}`).toLowerCase().includes(state.q.toLowerCase());
  }).sort((a,b)=>b.date.localeCompare(a.date)||b.id-a.id)}
  const badge=t=>`<span class="sd-status ${t.status}"><span class="sd-dot"></span>${statusNames[t.status]}</span>`;
  const priority=t=>`<span class="sd-priority ${['high','urgent'].includes(t.priority)?'high':t.priority}"><i aria-hidden="true"></i>${priorities[t.priority]}</span>`;
  const opts=(items,val)=>items.map(x=>{const [v,l]=Array.isArray(x)?x:[x,x];return `<option value="${esc(v)}" ${v===val?'selected':''}>${esc(l)}</option>`}).join('');
  function icons(){if(globalThis.lucide)globalThis.lucide.createIcons({attrs:{width:16,height:16}});}
  function notify(message){const box=root.querySelector('#sd-notice');box.hidden=false;box.innerHTML=`<span>${esc(message)}</span><button type="button" data-action="dismiss" aria-label="关闭提示">×</button>`;}
  function persist(){if(window.openai?.setWidgetState)window.openai.setWidgetState({modelContent:{design:'SAP Desk / 滚动叙事首页',role:state.view==='login'?'未登录':state.role,screen:state.view,density:design.density},privateContent:{version:3,userId:state.userId,view:state.view,ticket:state.ticket,scope:state.scope,design:{...design}}}).catch(()=>{});}
  function restore(saved){const p=saved?.privateContent;if(p?.version!==3)return;const user=accounts.find(a=>a.id===p.userId&&a.enabled);state.userId=user?.id||null;state.role=user?.role||'admin';state.view=user&&['list','detail','new','accounts','profile'].includes(p.view)?p.view:'login';state.ticket=Number.isInteger(p.ticket)&&tickets.some(t=>t.id===p.ticket)?p.ticket:1046;state.scope=['list','attention','archive'].includes(p.scope)?p.scope:'list';if(state.role==='admin'&&state.view==='new'||state.role!=='admin'&&state.view==='accounts')state.view='list';if(p.design?.density)design.density=p.design.density;if(typeof p.design?.grid==='boolean')design.grid=p.design.grid;if(Number.isFinite(p.design?.radius))design.radius=Math.max(0,Math.min(20,p.design.radius));}
  function applyDesign(){root.style.setProperty('--sd-row',design.density==='紧凑'?'16px':'24px');root.style.setProperty('--sd-radius',design.radius+'px');if(design.grid)root.style.removeProperty('--sd-gridline');else root.style.setProperty('--sd-gridline','transparent');}
  function chrome(){
    root.querySelector('#sd-role').value=state.view==='home'?'home':state.view==='login'?'login':state.view==='accounts'?'accounts':state.role;
    root.querySelector('#sd-list-label').textContent=state.role==='admin'?'全部工单':'我的工单';
    root.querySelector('#sd-user-name').textContent=current()?.name||'未登录';
    root.querySelector('#sd-user-role').textContent=state.role==='admin'?'管理员':'SAP 顾问';
    root.querySelector('#sd-user-avatar').textContent=current()?.name.slice(0,1)||'客';
    root.querySelector('#sd-total').textContent=visible().length;
    root.querySelector('#sd-attention').textContent=visible().filter(needs).length;
    root.querySelector('[data-nav=new]').hidden=state.role==='admin';
    root.querySelector('[data-nav=accounts]').hidden=state.role!=='admin';
    root.querySelector('#sd-admin-divider').hidden=state.role!=='admin';
    root.querySelectorAll('[data-nav]').forEach(b=>{if((['new','accounts','profile'].includes(state.view)?state.view:state.scope)===b.dataset.nav)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current')});
    const loc=state.view==='accounts'?'账号管理':state.view==='profile'?'我的账号':state.view==='new'?'提交需求':state.view==='detail'?'工单详情':state.scope==='archive'?'已归档':state.scope==='attention'?'需要我处理':state.role==='admin'?'全部工单':'我的工单';
    root.querySelector('#sd-location').textContent='工作空间 / '+loc;
  }
  function login(){
    root.querySelector('#sd-login-screen').innerHTML=`<section class="sd-home-login"><header class="sd-simple-login-header">${brand()}<button type="button" data-nav="home">${icon('arrow-left')}返回首页</button></header><div class="sd-simple-login-content"><section class="sd-login-panel" aria-label="登录"><h2>欢迎回来。</h2><p>登录你的 SAP 需求协作空间。</p><form id="sd-login-form"><div class="sd-field"><label for="sd-login-email">登录账号</label><input id="sd-login-email" name="email" type="email" autocomplete="off" placeholder="请输入账号邮箱" required></div><div class="sd-field"><label for="sd-login-password">密码</label><div class="sd-password-field"><input id="sd-login-password" name="password" type="password" autocomplete="off" placeholder="请输入密码" required><button type="button" data-action="show-password" aria-label="显示密码" aria-pressed="false">${icon('eye')}</button></div></div><div class="sd-form-meta"><span>账号由管理员统一开通</span><button class="sd-text-link" type="button" data-action="forgot-password" aria-expanded="false">忘记密码？</button></div><p class="sd-login-help" id="sd-login-help" hidden>请联系平台管理员重置密码，重置后首次登录需设置新密码。</p><p class="sd-error" id="sd-login-error" role="alert" hidden></p><button type="button" class="sd-button sd-primary sd-login-submit" data-local-submit="true">登录工作台 ${icon('arrow-right')}</button></form><details class="sd-demo-area"><summary>设计预览 · 使用演示账号</summary><div class="sd-flex"><button type="button" class="sd-button" data-demo-account="1">填入管理员账号</button><button type="button" class="sd-button" data-demo-account="2">填入顾问账号</button></div></details></section></div></section>`;icons();
  }

  function startSession(user,screen='list'){
    if(!user?.enabled)return;
    state={...state,userId:user.id,role:user.role,view:user.mustChange?'profile':screen,scope:'list',tab:'all',q:'',status:'',module:'',priority:'',owner:'',page:1,exportOpen:false};
    accountUI={q:'',role:'',status:'',editId:null,confirm:null};root.querySelector('#sd-login-screen').innerHTML='';root.querySelector('#sd-notice').hidden=true;render();window.scrollTo({top:0,behavior:'instant'});persist();
  }
  function signOut(){state.userId=null;state.view='login';draft={title:'',module:'MM',type:'配置',priority:'medium',due:'',description:'',files:[]};root.querySelector('#sd-notice').hidden=true;content.innerHTML='';render();window.scrollTo({top:0,behavior:'instant'});persist();}
  function accountEditor(){
    if(accountUI.editId===null)return '';
    const isNew=accountUI.editId==='new',u=isNew?{name:'',email:'',role:'consultant',enabled:true}:accounts.find(a=>a.id===accountUI.editId);
    if(!u)return '';
    const self=u.id===state.userId;
    return `<section class="sd-account-editor" aria-label="${isNew?'新增账号':'编辑账号'}"><div class="sd-editor-heading"><h3>${isNew?'新增账号':'管理账号 · '+esc(u.name)}</h3><button type="button" class="sd-quiet" data-action="close-account">收起 ${icon('x')}</button></div><form id="sd-account-form"><div class="sd-form-grid"><div class="sd-field"><label for="sd-account-name">姓名<span class="sd-required">*</span></label><input id="sd-account-name" name="name" required maxlength="30" placeholder="例如：王明" value="${esc(u.name)}"></div><div class="sd-field"><label for="sd-account-email">登录邮箱<span class="sd-required">*</span></label><input id="sd-account-email" name="email" type="email" required maxlength="120" placeholder="name@company.com" value="${esc(u.email)}"></div><div class="sd-field"><label for="sd-account-role">角色</label><select name="role" id="sd-account-role" ${self?'disabled':''}>${opts([['consultant','顾问 · 查看和回复自己的工单'],['admin','管理员 · 处理全部工单及管理账号']],u.role)}</select>${self?'<small>当前账号的角色保持为管理员。</small>':''}</div>${isNew?`<div class="sd-field"><label for="sd-account-initial">初始密码</label><input id="sd-account-initial" name="initial" type="text" value="Preview2026!" readonly><small>示例密码；首次登录需修改。</small></div>`:`<div class="sd-field"><label>账号状态</label><span class="sd-account-status ${u.enabled?'':'disabled'}"><span class="sd-dot"></span>${u.enabled?'已启用':'已停用'}</span><small>${u.mustChange?'下次登录需修改密码':'账号由管理员统一维护'}</small></div>`}</div><div id="sd-account-error" class="sd-error" role="alert" hidden></div><div class="sd-form-bottom"><div class="sd-editor-ops">${!isNew&&!self?`<button type="button" class="sd-text-link" data-account-op="reset">重置密码</button><button type="button" class="${u.enabled?'sd-danger-link':'sd-text-link'}" data-account-op="${u.enabled?'disable':'enable'}">${u.enabled?'停用':'启用'}账号</button>`:'<p>账号开通后可使用邮箱登录。</p>'}</div><button type="button" class="sd-button sd-primary" data-local-submit="true">${isNew?'创建账号':'保存修改'} ${icon('arrow-up-right')}</button></div></form>${accountConfirmation(u)}</section>`;
  }
  function accountConfirmation(u){const action=accountUI.confirm;if(!action)return '';if(action==='reset-done')return `<div class="sd-inline-confirm" role="status"><p>${esc(u.name)} 的示例密码已重置。下次登录时需设置新密码。</p><code>Reset2026!demo</code><button type="button" class="sd-button" data-action="close-account-confirm">知道了</button></div>`;return `<div class="sd-inline-confirm"><p>${action==='reset'?'重置 '+esc(u.name)+' 的密码？原密码将失效，下次登录需修改密码。':'停用 '+esc(u.name)+' 的账号？账号将无法登录，已提交工单与历史记录继续保留。'}</p><div class="sd-flex"><button type="button" class="sd-button sd-primary" data-confirm-account="${action}">${action==='reset'?'确认重置':'确认停用'}</button><button type="button" class="sd-button" data-action="close-account-confirm">取消</button></div></div>`;}
  function accountList(){
    if(state.role!=='admin')return;
    content.innerHTML=`<div class="sd-heading"><div><div class="sd-eyebrow">PEOPLE / ACCESS</div><h1>账号管理</h1><p class="sd-subtitle">让合适的人，进入同一个协作空间。</p></div><button type="button" class="sd-button sd-primary" data-action="new-account">${icon('user-plus')}新增账号</button></div><p class="sd-account-intro"><strong>${accounts.length} 位成员</strong> · ${accounts.filter(a=>a.role==='admin').length} 位管理员 <span style="margin-left:12px">账号统一开通，权限按角色分配。</span></p>${accountEditor()}<div class="sd-filter-row"><label class="sd-search">${icon('search')}<input type="search" id="sd-account-search" aria-label="搜索账号" placeholder="搜索姓名或邮箱" value="${esc(accountUI.q)}"></label><select id="sd-account-filter-role" aria-label="筛选账号角色">${opts([['','全部角色'],['admin','管理员'],['consultant','顾问']],accountUI.role)}</select><select id="sd-account-filter-status" aria-label="筛选账号状态">${opts([['','全部状态'],['enabled','已启用'],['disabled','已停用']],accountUI.status)}</select></div><div id="sd-account-results" aria-live="polite"></div><details class="sd-audit"><summary>账号操作记录</summary><ul>${accountLogs.slice(0,8).map(l=>`<li><span>${esc(l.text)}</span><time>${esc(l.time)}</time></li>`).join('')}</ul></details>`;accountRows();icons();
  }
  function accountRows(){const rows=accounts.filter(a=>(!accountUI.q||(a.name+' '+a.email).toLowerCase().includes(accountUI.q.toLowerCase()))&&(!accountUI.role||a.role===accountUI.role)&&(!accountUI.status||a.enabled===(accountUI.status==='enabled')));root.querySelector('#sd-account-results').innerHTML=rows.length?`<table class="sd-accounts-table" aria-label="平台账号"><thead><tr><th scope="col">成员 / 登录邮箱</th><th scope="col">角色</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead><tbody>${rows.map(u=>`<tr><td><div class="sd-account-name"><div class="sd-avatar">${esc(u.name.slice(0,1))}</div><div class="sd-person">${esc(u.name)}${u.id===state.userId?'<span class="sd-self-tag">你</span>':''}<small>${esc(u.email)}</small></div></div></td><td><span class="sd-role-chip">${u.role==='admin'?'管理员':'顾问'}</span></td><td><span class="sd-account-status ${u.enabled?'':'disabled'}"><span class="sd-dot"></span>${u.enabled?'已启用':'已停用'}</span>${u.mustChange?'<span class="sd-account-next">首次登录需改密</span>':''}</td><td><button type="button" class="sd-button" data-edit-account="${u.id}">管理 ${icon('chevron-right')}</button></td></tr>`).join('')}</tbody></table>`:'<div class="sd-empty"><p>没有找到符合条件的账号。</p></div>';icons();}
  function profile(){const u=current();if(!u)return;content.innerHTML=`<div class="sd-profile"><div class="sd-heading"><div><div class="sd-eyebrow">MY ACCOUNT / SECURITY</div><h1>${u.mustChange?'设置新密码':'我的账号'}</h1><p class="sd-subtitle">${u.mustChange?'为你的账号设置新密码，然后进入工作台。':'查看账号信息，管理你的登录密码。'}</p></div></div><div class="sd-profile-identity"><div class="sd-avatar">${esc(u.name.slice(0,1))}</div><div class="sd-person">${esc(u.name)} · ${u.role==='admin'?'管理员':'顾问'}<small>${esc(u.email)}</small></div></div><form id="sd-password-form">${u.mustChange?'':`<div class="sd-field"><label for="sd-old-password">当前密码</label><input type="password" id="sd-old-password" name="oldPassword" required autocomplete="off"></div>`}<div class="sd-field"><label for="sd-new-password">新密码</label><input type="password" id="sd-new-password" name="newPassword" minlength="12" required autocomplete="off" placeholder="至少 12 个字符"><small>建议组合字母、数字和符号。</small></div><div class="sd-field"><label for="sd-confirm-password">确认新密码</label><input type="password" id="sd-confirm-password" name="confirmation" minlength="12" required autocomplete="off" placeholder="再次输入新密码"></div><p id="sd-password-error" class="sd-error" role="alert" hidden></p><div class="sd-profile-foot"><span class="sd-small sd-muted">${u.mustChange?'完成后即可开始协作':'修改后使用新密码登录'}</span><button class="sd-button sd-primary" type="button" data-local-submit="true">${u.mustChange?'保存并进入工作台':'更新密码'} ${icon('arrow-right')}</button></div></form></div>`;icons();}
  function accountLog(text){accountLogs.unshift({time:now(),text:current().name+' '+text});}
  function accountError(message){const box=root.querySelector('#sd-account-error');if(box){box.hidden=false;box.textContent=message;}}
  function attachments(files){return files.map(f=>`<button type="button" class="sd-attachment" data-action="attachment" data-name="${esc(f.name)}">${icon('file-text')}<span>${esc(f.name)}<small>${esc(f.size)}</small></span></button>`).join('')}
  function list(){
    const own=visible(),attention=own.filter(needs).length;
    const title=state.scope==='archive'?'已归档':state.scope==='attention'?'需要我处理':state.role==='admin'?'需求工单':'我的需求';
    content.innerHTML=`<div class="sd-heading"><div><div class="sd-eyebrow">REQUESTS / WORKSPACE</div><h1>${title}</h1><p class="sd-subtitle">${state.role==='admin'?'从需求到交付，让每一步都有回应。':'把需求说明白，让协作更从容。'}</p></div><div class="sd-flex">${state.role==='admin'?`<button type="button" class="sd-button" data-action="export">${icon('download')}导出</button>`:`<button type="button" class="sd-button sd-primary" data-nav="new">${icon('plus')}提交需求</button>`}</div></div>
    <div class="sd-focus-strip"><div class="sd-flex"><span class="sd-dot" style="color:var(--sd-green)"></span><strong>${attention?'有 '+attention+' 项需求，等待你的下一步。':'所有需求都已得到回应。'}</strong><span>${state.role==='admin'?'受理需求 · 回复补充':'查看反馈 · 补充说明'}</span></div><button type="button" data-nav="attention">去处理 ${icon('arrow-up-right')}</button></div>
    ${state.exportOpen?`<div class="sd-inline-panel"><h3>导出当前筛选结果 · ${filtered().length} 条</h3><p>工单列表：编号、标题、模块、类型、优先级、状态、提交人、期望日期、创建与更新时间。沟通记录：每条回复与状态变化的操作人、内容和时间。</p><div class="sd-flex"><button class="sd-button" type="button" data-action="export-demo">工单列表 · CSV</button><button class="sd-button" type="button" data-action="export-demo">沟通记录 · CSV</button><button class="sd-quiet" type="button" data-action="export">收起</button></div></div>`:''}
    <div class="sd-tabs" aria-label="工单快捷筛选">${[['all','全部',own.length],['active','进行中',own.filter(t=>!['done','closed'].includes(t.status)).length],['unread','未读更新',own.filter(t=>t.unread.includes(state.role)).length]].map(([id,label,count])=>`<button type="button" data-tab="${id}" aria-pressed="${state.tab===id}">${label}<small>${count}</small></button>`).join('')}</div>
    <div class="sd-filter-row"><label class="sd-search">${icon('search')}<input type="search" id="sd-search" aria-label="搜索工单" placeholder="搜索编号、标题或沟通内容" value="${esc(state.q)}"></label><select id="sd-filter-module" aria-label="按 SAP 模块筛选">${opts([['','全部模块'],...modules],state.module)}</select><select id="sd-filter-status" aria-label="按状态筛选">${opts([['','全部状态'],...Object.entries(statusNames)],state.status)}</select><button type="button" class="sd-filter-more" data-action="filters" aria-expanded="${state.extra}">${icon('sliders-horizontal')}筛选</button></div>
    <div class="sd-extra-filters" ${state.extra?'':'hidden'}><label>优先级<select id="sd-filter-priority">${opts([['','全部优先级'],...Object.entries(priorities)],state.priority)}</select></label>${state.role==='admin'?`<label>提交人<select id="sd-filter-owner">${opts([['','全部提交人'],...accounts.filter(a=>a.role==='consultant').map(a=>a.name)],state.owner)}</select></label>`:''}<button type="button" class="sd-quiet" data-action="clear-filters">清除全部筛选</button></div>
    <div id="sd-results" aria-live="polite"></div>`;
    rows();icons();
  }
  function rows(){const all=filtered();const size=5,pages=Math.max(1,Math.ceil(all.length/size));state.page=Math.min(state.page,pages);const items=all.slice((state.page-1)*size,state.page*size);const results=root.querySelector('#sd-results');if(!results)return;
    results.innerHTML=items.length?`<table class="sd-table" aria-label="需求工单列表"><thead><tr><th scope="col">需求 / 最近更新</th><th scope="col">模块</th><th scope="col">优先级</th><th scope="col">状态</th><th scope="col">提交人</th></tr></thead><tbody>${items.map(t=>`<tr><td><button type="button" class="sd-ticket-title" data-ticket="${t.id}">${esc(t.title)}</button><div class="sd-ticket-sub"><span class="sd-mono">REQ-${t.id}</span><span>·</span><span>${esc(t.date)}</span>${t.unread.includes(state.role)?'<span class="sd-dot"></span><span>新动态</span>':''}</div></td><td><span class="sd-module">${t.module}</span></td><td>${priority(t)}</td><td>${badge(t)}</td><td>${esc(t.owner)}</td></tr>`).join('')}</tbody></table><div class="sd-list-footer"><span>显示 ${(state.page-1)*size+1}–${Math.min(state.page*size,all.length)} 条，共 ${all.length} 条</span><div class="sd-page-step"><button type="button" data-action="prev" aria-label="上一页" ${state.page===1?'disabled':''}>${icon('chevron-left')}</button><span>${state.page} / ${pages}</span><button type="button" data-action="next" aria-label="下一页" ${state.page===pages?'disabled':''}>${icon('chevron-right')}</button></div></div>`:`<div class="sd-empty"><p>这里暂时没有符合条件的工单。</p><button type="button" class="sd-button" data-action="clear-filters">清除筛选</button></div>`;icons();}
  function detail(){const t=visible().find(x=>x.id===state.ticket);if(!t){state.view='list';list();return;}const isClosed=t.status==='closed';
    content.innerHTML=`<div class="sd-breadcrumb"><button type="button" class="sd-quiet" data-action="back">${icon('arrow-left')}返回列表</button></div><div class="sd-flex"><span class="sd-eyebrow" style="margin:0">REQ-${t.id}</span>${badge(t)}</div><div class="sd-detail-title">${esc(t.title)}</div><div class="sd-flex sd-small sd-muted"><span class="sd-module">${t.module}</span><span>${t.type}</span><span>·</span><span>${t.owner} 提交</span></div>
    ${t.next==='consultant'&&t.status==='waiting'?`<div class="sd-next">${state.role==='admin'?'下一步：等待顾问确认或补充说明。':'轮到你了：请确认结果，或继续补充说明。'}</div>`:''}
    <div class="sd-detail-grid"><div><div class="sd-section-label">需求说明</div><p class="sd-description">${esc(t.description)}</p><div>${attachments(t.files)}</div><div class="sd-section-label" style="margin-top:30px">沟通时间线 · ${t.events.length} 条记录</div><ol class="sd-timeline">${t.events.map(e=>e.kind==='reply'?`<li><span class="sd-time-avatar">${e.actor===admin?'管':esc(e.actor.slice(0,1))}</span><div class="sd-message-head"><span>${esc(e.actor)}</span><time>${esc(e.time)}</time></div><div class="sd-message ${e.actor===admin?'sd-admin':''}">${esc(e.text)}</div>${e.files?.length?`<div style="margin-top:8px">${attachments(e.files)}</div>`:''}</li>`:`<li class="sd-event"><span class="sd-time-avatar"></span><div class="sd-event-text">${esc(e.actor)} ${esc(e.text)}<br><time>${esc(e.time)}</time></div></li>`).join('')}</ol>
    ${isClosed&&state.role==='consultant'?'<div class="sd-next">此工单已关闭，沟通记录与附件可继续查看。</div>':`<form id="sd-reply-form" class="sd-composer"><label for="sd-reply">${isClosed?'重新打开工单':'继续沟通'}</label><textarea id="sd-reply" name="reply" maxlength="10000" placeholder="${isClosed?'填写重新打开的原因…':state.role==='admin'?'写下处理进展、反馈或需要补充的信息…':'补充业务背景、测试结果或确认意见…'}" ${isClosed?'required':''}></textarea><div id="sd-reply-error" class="sd-error" role="alert" hidden></div><div class="sd-composer-actions">${state.role==='admin'?`<select name="targetStatus" aria-label="回复后的状态">${opts(isClosed?[['in-progress','重新打开 → 处理中']]:[[t.status,'保持「'+statusNames[t.status]+'」'],...transitions[t.status].map(s=>[s,'转为「'+statusNames[s]+'」'])],isClosed?'in-progress':t.status)}</select>`:`<span class="sd-small sd-muted">回复仅对你与管理员可见</span>`}<button type="button" data-local-submit="true" class="sd-button sd-primary">${isClosed?'重新打开':state.role==='admin'?'发送反馈':'发送回复'} ${icon('arrow-up-right')}</button></div>${!isClosed?`<label class="sd-small sd-muted" style="margin-top:14px">补充附件 · 最多 5 个，每个 20 MB<input type="file" name="replyFiles" multiple accept=".png,.jpg,.jpeg,.pdf,.docx,.xlsx,.csv,.txt" style="font-size:10px;width:100%;margin-top:5px"></label>`:''}</form>`}</div>
    <aside class="sd-detail-meta" aria-label="工单属性"><dl class="sd-meta-pair"><dt>当前状态</dt><dd>${badge(t)}</dd></dl><dl class="sd-meta-pair"><dt>优先级</dt><dd>${priority(t)}</dd></dl><dl class="sd-meta-pair"><dt>期望完成</dt><dd>${t.due||'未指定'}</dd></dl><dl class="sd-meta-pair"><dt>提交人</dt><dd>${esc(t.owner)}</dd></dl><dl class="sd-meta-pair"><dt>SAP 模块</dt><dd>${t.module}</dd></dl><dl class="sd-meta-pair"><dt>需求类型</dt><dd>${t.type}</dd></dl></aside></div>`;icons();
  }
  function newTicket(){content.innerHTML=`<div class="sd-form-wrap"><div class="sd-breadcrumb"><button class="sd-quiet" type="button" data-action="back">${icon('arrow-left')}返回我的需求</button></div><div class="sd-heading"><div><div class="sd-eyebrow">A NEW REQUEST</div><h1>说说你的需求。</h1><p class="sd-subtitle">交代背景、预期与验收标准，协作从这里开始。</p></div></div><form id="sd-new-form"><div class="sd-form-section"><h3><span>01</span>基本信息</h3><div class="sd-form-grid"><div class="sd-field sd-wide"><label for="sd-title">标题<span class="sd-required">*</span></label><input id="sd-title" name="title" required maxlength="120" placeholder="例如：采购订单审批策略调整" value="${esc(draft.title)}"></div><div class="sd-field"><label for="sd-module">SAP 模块<span class="sd-required">*</span></label><select id="sd-module" name="module">${opts(modules,draft.module)}</select></div><div class="sd-field"><label for="sd-type">需求类型<span class="sd-required">*</span></label><select id="sd-type" name="type">${opts(types,draft.type)}</select></div><div class="sd-field"><label for="sd-priority">优先级<span class="sd-required">*</span></label><select id="sd-priority" name="priority">${opts([['low','低 · 一般优化'],['medium','中 · 常规需求'],['high','高 · 影响关键业务'],['urgent','紧急 · 业务中断']],draft.priority)}</select></div><div class="sd-field"><label for="sd-due">期望完成日期</label><input id="sd-due" name="due" type="date" min="2026-10-09" value="${esc(draft.due)}"><small>可选，作为排期参考</small></div></div></div><div class="sd-form-section"><h3><span>02</span>需求说明</h3><div class="sd-field"><label for="sd-description">详细描述<span class="sd-required">*</span></label><textarea id="sd-description" name="description" required maxlength="10000" placeholder="业务背景：目前遇到了什么问题？&#10;&#10;期望结果：希望如何处理？&#10;&#10;验收标准：怎样算完成？">${esc(draft.description)}</textarea></div></div><div class="sd-form-section"><h3><span>03</span>相关附件 <span>可选</span></h3><div class="sd-upload">${icon('upload')}<div style="min-width:0;flex:1"><strong>截图、需求说明或参考表格</strong><p class="sd-small sd-muted">最多 5 个文件，每个不超过 20 MB</p><input aria-label="选择附件" type="file" id="sd-files" name="files" multiple accept=".png,.jpg,.jpeg,.pdf,.docx,.xlsx,.csv,.txt"></div></div><div id="sd-files-info" class="sd-small sd-muted" style="margin-top:9px"></div></div><div id="sd-new-error" class="sd-error" role="alert" hidden></div><div class="sd-form-bottom"><p>提交后进入「待受理」，可在时间线继续补充。</p><button type="button" data-local-submit="true" class="sd-button sd-primary">提交需求 ${icon('arrow-up-right')}</button></div></form></div>`;icons();}
  function render(){
    if(!current()?.enabled&&state.view!=='home')state.view='login';
    if(!['login','home'].includes(state.view)&&current()?.mustChange)state.view='profile';
    if(state.view==='accounts'&&state.role!=='admin')state.view='list';
    const signedOut=['home','login'].includes(state.view);root.querySelector('#sd-home-screen').hidden=state.view!=='home';root.querySelector('#sd-login-screen').hidden=state.view!=='login';root.querySelector('#sd-workspace').hidden=signedOut;chrome();
    if(state.view==='home')icons();else if(state.view==='login')login();else if(state.view==='accounts')accountList();else if(state.view==='profile')profile();else if(state.view==='detail')detail();else if(state.view==='new')newTicket();else list();
    applyDesign();root.querySelectorAll('button,summary').forEach(el=>el.classList.add('cursor-interaction'));
  }
  function navigate(view,scope){state.view=current()?.mustChange?'profile':view;if(scope)state.scope=scope;state.page=1;state.exportOpen=false;render();window.scrollTo({top:0,behavior:'instant'});persist();}
  function validateFiles(files){if(files.length>5)return '每次最多添加 5 个附件。';for(const f of files){if(f.size>20*1024*1024)return `${f.name} 超过 20 MB。`;if(!/\.(png|jpe?g|pdf|docx|xlsx|csv|txt)$/i.test(f.name))return `${f.name} 的格式暂不支持。`;}return '';}
  function fileMeta(files){return Array.from(files).map(f=>({name:f.name,size:f.size<1024*1024?Math.max(1,Math.round(f.size/1024))+' KB':(f.size/1024/1024).toFixed(1)+' MB'}));}
  let eventClock=0;
  function now(){eventClock++;return '2026-10-09 15:'+String(30+Math.floor(eventClock/60)).padStart(2,'0')+':'+String(eventClock%60).padStart(2,'0');}
  root.addEventListener('click',event=>{
    const b=event.target.closest('button');if(!b||!root.contains(b))return;
    if(b.dataset.localSubmit){event.preventDefault();const form=b.closest('form');if(form?.reportValidity())form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));return;}
    if(b.dataset.demoAccount){const u=accounts.find(a=>a.id===Number(b.dataset.demoAccount));root.querySelector('#sd-login-email').value=u.email;root.querySelector('#sd-login-password').value=u.password;root.querySelector('#sd-login-error').hidden=true;return;}
    if(b.dataset.nav){const nav=b.dataset.nav;if(nav==='home'){state.view='home';render();window.scrollTo({top:0,behavior:'instant'});persist();}else if(nav==='login'){navigate(current()?.enabled?'list':'login');}else if(nav==='accounts'){if(state.role==='admin')navigate('accounts');}else if(nav==='profile')navigate('profile');else if(nav==='new'){if(state.role==='consultant')navigate('new');}else{state.tab='all';state.q='';state.module='';state.status='';state.priority='';state.owner='';navigate('list',nav);}return;}
    if(b.dataset.editAccount&&state.role==='admin'){accountUI.editId=Number(b.dataset.editAccount);accountUI.confirm=null;accountList();return;}
    if(b.dataset.accountOp&&state.role==='admin'){
      const u=accounts.find(a=>a.id===accountUI.editId);if(!u||u.id===state.userId)return;
      if(b.dataset.accountOp==='enable'){u.enabled=true;accountLog('启用了 '+u.name+' 的账号。');accountList();notify('示例账号已启用。');}
      else{accountUI.confirm=b.dataset.accountOp;accountList();}return;
    }
    if(b.dataset.confirmAccount&&state.role==='admin'){
      const u=accounts.find(a=>a.id===accountUI.editId);if(!u||u.id===state.userId)return;
      if(b.dataset.confirmAccount==='disable'){
        if(u.role==='admin'&&!accounts.some(a=>a.id!==u.id&&a.role==='admin'&&a.enabled)){accountError('请至少保留一个已启用的管理员账号。');return;}
        u.enabled=false;accountUI.confirm=null;accountLog('停用了 '+u.name+' 的账号，历史工单保留。');accountList();notify('示例账号已停用，工单与沟通记录已保留。');
      }else if(b.dataset.confirmAccount==='reset'){u.password='Reset2026!demo';u.mustChange=true;accountUI.confirm='reset-done';accountLog('重置了 '+u.name+' 的密码，下次登录需改密。');accountList();}return;
    }
    if(b.dataset.ticket){state.ticket=Number(b.dataset.ticket);const t=visible().find(t=>t.id===state.ticket);if(!t)return;t.unread=t.unread.filter(r=>r!==state.role);navigate('detail');return;}
    if(b.dataset.tab){state.tab=b.dataset.tab;state.page=1;render();persist();return;}
    switch(b.dataset.action){
      case 'sign-out':signOut();break;
      case 'show-password':{const input=root.querySelector('#sd-login-password');const visible=input.type==='password';input.type=visible?'text':'password';b.setAttribute('aria-pressed',String(visible));b.setAttribute('aria-label',visible?'隐藏密码':'显示密码');b.innerHTML=icon(visible?'eye-off':'eye');icons();break;}
      case 'forgot-password':{const help=root.querySelector('#sd-login-help');help.hidden=!help.hidden;b.setAttribute('aria-expanded',String(!help.hidden));break;}
      case 'new-account':if(state.role==='admin'){accountUI.editId='new';accountUI.confirm=null;accountList();}break;
      case 'close-account':accountUI.editId=null;accountUI.confirm=null;accountList();break;
      case 'close-account-confirm':accountUI.confirm=null;accountList();break;
      case 'dismiss':root.querySelector('#sd-notice').hidden=true;break;
      case 'back':navigate('list');break;
      case 'filters':state.extra=!state.extra;list();break;
      case 'clear-filters':state.q='';state.module='';state.status='';state.priority='';state.owner='';state.tab='all';state.page=1;state.scope='list';render();break;
      case 'prev':state.page=Math.max(1,state.page-1);rows();break;
      case 'next':state.page++;rows();break;
      case 'export':if(state.role==='admin'){state.exportOpen=!state.exportOpen;list();}break;
      case 'export-demo':notify('设计预览：导出范围为当前筛选的 '+filtered().length+' 条工单；正式版提供 CSV 下载。');break;
      case 'attachment':notify('设计预览：'+b.dataset.name+' 为示例附件，当前未连接文件存储。');break;
    }
  });
  root.addEventListener('input',event=>{const el=event.target;if(el.id==='sd-account-search'){accountUI.q=el.value;accountRows();}if(el.id==='sd-search'){state.q=el.value;state.page=1;rows();}if(el.closest('#sd-new-form')&&el.name!=='files')draft[el.name]=el.value;});
  root.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.tagName==='INPUT'&&!['file','checkbox','button'].includes(event.target.type)){const form=event.target.closest('form');if(form){event.preventDefault();if(form.reportValidity())form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));}}});
  root.addEventListener('change',event=>{
    const el=event.target;
    if(el.id==='sd-role'){if(el.value==='login')signOut();else{const user=accounts.find(a=>a.id===(el.value==='consultant'?2:1));if(user.enabled)startSession(user,el.value==='accounts'?'accounts':'list');else{signOut();const error=root.querySelector('#sd-login-error');error.hidden=false;error.textContent='该示例账号已停用，请使用管理员账号启用。';}}return;}
    if(el.id==='sd-account-filter-role'){accountUI.role=el.value;accountRows();}
    if(el.id==='sd-account-filter-status'){accountUI.status=el.value;accountRows();}
    const map={'sd-filter-module':'module','sd-filter-status':'status','sd-filter-priority':'priority','sd-filter-owner':'owner'};if(map[el.id]){state[map[el.id]]=el.value;state.page=1;rows();if(state.exportOpen)list();}
    if(el.id==='sd-files'){const err=validateFiles(el.files);draft.files=err?[]:fileMeta(el.files);root.querySelector('#sd-files-info').textContent=err||draft.files.map(f=>f.name+' · '+f.size).join('；');if(err)el.value='';}
    if(el.closest('#sd-new-form')&&el.name!=='files')draft[el.name]=el.value;
  });
  root.addEventListener('submit',event=>{
    event.preventDefault();
    if(event.target.id==='sd-login-form'){
      const data=new FormData(event.target),email=String(data.get('email')||'').trim().toLowerCase(),password=String(data.get('password')||'');
      const user=accounts.find(a=>a.email.toLowerCase()===email&&a.password===password);
      if(!user||!user.enabled){const error=root.querySelector('#sd-login-error');error.hidden=false;error.textContent='账号、密码有误或账号已停用，请检查后重试。';return;}
      event.target.reset();startSession(user);return;
    }
    if(event.target.id==='sd-account-form'){
      if(state.role!=='admin'||!current()||current().mustChange)return;
      const data=new FormData(event.target),isNew=accountUI.editId==='new',u=isNew?null:accounts.find(a=>a.id===accountUI.editId);
      const name=String(data.get('name')||'').trim(),email=String(data.get('email')||'').trim().toLowerCase(),role=String(data.get('role')||u?.role||'consultant');
      if(!name||!email){accountError('请填写姓名与登录邮箱。');return;}
      if(accounts.some(a=>a.id!==u?.id&&a.email.toLowerCase()===email)){accountError('此登录邮箱已存在，请使用其他邮箱。');return;}
      if(!['admin','consultant'].includes(role))return;
      if(u?.id===state.userId&&role!==u.role){accountError('当前账号不能修改自身角色。');return;}
      if(u?.role==='admin'&&u.enabled&&role!=='admin'&&!accounts.some(a=>a.id!==u.id&&a.role==='admin'&&a.enabled)){accountError('请至少保留一个已启用的管理员账号。');return;}
      if(isNew){accounts.push({id:Math.max(...accounts.map(a=>a.id))+1,name,email,role,enabled:true,mustChange:true,password:'Preview2026!'});accountLog('创建了 '+name+' 的'+(role==='admin'?'管理员':'顾问')+'账号。');}
      else{Object.assign(u,{name,email,role});accountLog('更新了 '+name+' 的账号信息。');}
      accountUI.editId=null;accountUI.confirm=null;accountUI.q='';accountUI.role='';accountUI.status='';render();notify(isNew?'示例账号已创建，初始密码为 Preview2026!，首次登录需修改。':'示例账号信息已更新。');persist();return;
    }
    if(event.target.id==='sd-password-form'){
      const u=current();if(!u)return;const data=new FormData(event.target),old=String(data.get('oldPassword')||''),password=String(data.get('newPassword')||''),confirm=String(data.get('confirmation')||'');
      let message='';if(!u.mustChange&&old!==u.password)message='当前密码不正确。';else if(password.length<12)message='新密码至少需要 12 个字符。';else if(password!==confirm)message='两次输入的新密码不一致。';else if(password===u.password)message='新密码需要与原密码不同。';
      if(message){const box=root.querySelector('#sd-password-error');box.hidden=false;box.textContent=message;return;}
      const first=u.mustChange;u.password=password;u.mustChange=false;accountLog('更新了自己的登录密码。');navigate('list');notify(first?'示例密码已更新，欢迎进入工作台。':'本次预览的登录密码已更新。');return;
    }
    if(event.target.id==='sd-new-form'){
      event.preventDefault();if(state.role!=='consultant')return;const form=event.target,data=new FormData(form);const title=String(data.get('title')||'').trim(),description=String(data.get('description')||'').trim();const err=validateFiles(form.elements.files.files)||(!title||!description?'请填写标题和详细描述。':'');if(err){const box=root.querySelector('#sd-new-error');box.hidden=false;box.textContent=err;return;}
      const time=now(),id=Math.max(...tickets.map(t=>t.id))+1;tickets.unshift({id,title,description,module:data.get('module'),type:data.get('type'),priority:data.get('priority'),due:data.get('due'),status:'pending',owner:current().name,ownerId:state.userId,date:time.slice(5,16),next:'admin',unread:['admin'],files:fileMeta(form.elements.files.files),events:[{kind:'event',actor:current().name,time,text:'提交了需求工单'}]});state.ticket=id;draft={title:'',module:'MM',type:'配置',priority:'medium',due:'',description:'',files:[]};navigate('detail');notify('已在本次预览中提交 REQ-'+id+'，管理员视角可查看。');return;
    }
    if(event.target.id==='sd-reply-form'){
      event.preventDefault();const t=visible().find(t=>t.id===state.ticket);if(!t||t.status==='closed'&&state.role!=='admin')return;const data=new FormData(event.target),body=String(data.get('reply')||'').trim(),target=state.role==='admin'?String(data.get('targetStatus')):t.status;const files=event.target.elements.replyFiles?.files||[];let err=validateFiles(files);if(!body)err=t.status==='closed'?'请填写重新打开的原因。':'请填写反馈、补充说明或状态变更原因。';if(target!==t.status&&!transitions[t.status].includes(target))err='当前状态不支持此变更。';if(err){const box=root.querySelector('#sd-reply-error');box.hidden=false;box.textContent=err;return;}
      const time=now(),actor=current().name;t.events.push({kind:'reply',actor,time,text:body,files:fileMeta(files)});if(target!==t.status){t.events.push({kind:'event',actor,time,text:`将状态从「${statusNames[t.status]}」变更为「${statusNames[target]}」`});t.status=target;}t.date=time.slice(5,16);t.unread=[state.role==='admin'?'consultant':'admin'];t.next=['done','closed'].includes(t.status)?'none':state.role==='consultant'?'admin':t.status==='waiting'?'consultant':'admin';render();notify(t.status==='closed'?'本次预览：工单已关闭，历史记录已保留。':'本次预览：回复与状态已记录在时间线。');persist();
    }
  });
  restore(window.openai?.widgetState);render();
  window.addEventListener('openai:set_globals',event=>{if(event.detail?.globals?.widgetState){restore(event.detail.globals.widgetState);render();}});
  if(globalThis.Tweak){const tweak=new Tweak({container:root,onChange:()=>{applyDesign();persist();}});tweak.addSelect(design,'density',{label:'列表密度',options:['舒展','紧凑']});tweak.addSlider(design,'radius',{label:'面板圆角',min:0,max:20,step:2,unit:'px'});tweak.addToggle(design,'grid',{label:'浅网格背景'});}
})();
