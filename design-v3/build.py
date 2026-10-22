from pathlib import Path
import re

root = Path(__file__).resolve().parent
source = root / 'workspace-source.html'
text = source.read_text()
css = re.search(r'<style>(.*?)</style>', text, re.S).group(1)
css = re.sub(r"@import url\([^;]+;", '', css)
shell = text.split('</style>', 1)[1].split('<script>', 1)[0]
app = re.search(r'<script>(.*?)</script>', text, re.S).group(1)

shell = shell.replace('DESIGN STUDY / 02', 'DESIGN STUDY / 03')
shell = shell.replace('<option value="login">', '<option value="home">首页</option><option value="login">', 1)
shell = shell.replace('<div id="sd-login-screen" class="sd-login"></div>', (root / 'landing.html').read_text() + '\n<div id="sd-login-screen" class="sd-login" hidden></div>')
shell = shell.replace('<div class="sd-sidebar-bottom">', '<div class="sd-sidebar-bottom"><button type="button" class="sd-back-home" data-nav="home"><i data-lucide="arrow-left" aria-hidden="true"></i>返回首页</button>')

app = app.replace("userId:null,role:'admin',view:'login'", "userId:null,role:'admin',view:'home'")
app = app.replace("design:'SAP Desk / 奶油白与珊瑚橙'", "design:'SAP Desk / 滚动叙事首页'")
app = app.replace('version:2', 'version:3').replace('p?.version!==2', 'p?.version!==3')
app = app.replace("state.view==='login'?'login':", "state.view==='home'?'home':state.view==='login'?'login':")
start = app.index('  function login(){')
end = app.index('  function startSession(', start)
app = app[:start] + (root / 'login.template.js').read_text() + '\n' + app[end:]
app = app.replace("if(!current()?.enabled)state.view='login';", "if(!current()?.enabled&&state.view!=='home')state.view='login';")
app = app.replace("if(state.view!=='login'&&current()?.mustChange)", "if(!['login','home'].includes(state.view)&&current()?.mustChange)")
app = app.replace("const signedOut=state.view==='login';root.querySelector('#sd-login-screen').hidden=!signedOut;root.querySelector('#sd-workspace').hidden=signedOut;chrome();", "const signedOut=['home','login'].includes(state.view);root.querySelector('#sd-home-screen').hidden=state.view!=='home';root.querySelector('#sd-login-screen').hidden=state.view!=='login';root.querySelector('#sd-workspace').hidden=signedOut;chrome();")
app = app.replace("if(signedOut)login();else if(state.view==='accounts')", "if(state.view==='home')icons();else if(state.view==='login')login();else if(state.view==='accounts')")
app = app.replace("if(b.dataset.nav){const nav=b.dataset.nav;if(nav==='accounts')", "if(b.dataset.nav){const nav=b.dataset.nav;if(nav==='home'){state.view='home';render();window.scrollTo({top:0,behavior:'instant'});persist();}else if(nav==='login'){navigate(current()?.enabled?'list':'login');}else if(nav==='accounts')")
app = app.replace("state.exportOpen=false;render();persist();}", "state.exportOpen=false;render();window.scrollTo({top:0,behavior:'instant'});persist();}")
app = app.replace("content.innerHTML='';render();persist();}", "content.innerHTML='';render();window.scrollTo({top:0,behavior:'instant'});persist();}")
app = app.replace("render();persist();\n  }\n  function signOut", "render();window.scrollTo({top:0,behavior:'instant'});persist();\n  }\n  function signOut")

(root / 'workspace.css').write_text(css)
(root / 'app.js').write_text(app)
(root / 'index.html').write_text((root / 'index.template.html').read_text().replace('<!-- APP_SHELL -->', shell))
print('Built index.html, workspace.css, app.js')
