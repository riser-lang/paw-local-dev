// Extend the existing sidebar without changing the compiled legacy application.
(()=>{
 function link(text,href){const a=document.createElement('a');a.textContent=text;a.href=href;a.style.cssText='display:block;padding:8px 16px;color:inherit;font:inherit;text-decoration:none';return a;}
 function connect(){
  const sidebar=document.querySelector('aside nav')||document.querySelector('aside')||document.querySelector('nav');
  if(sidebar&&!document.getElementById('paw-pm-navigation')){
   const group=document.createElement('div');group.id='paw-pm-navigation';group.setAttribute('aria-label','项目管理');
   group.append(link('驾驶舱','/app/pm/today'),link('资料去向','/app/pm/receipts'));sidebar.append(group);
  }
  const group=document.getElementById('paw-pm-navigation');
  if(group){
   let projectLink=group.querySelector('[data-current-pm-dossier]');
   const current=location.pathname.match(/^\/app\/projects\/([^/?#]+)$/);
   if(current&&!projectLink){projectLink=link('本项目档案','/app/pm/projects/'+current[1]);projectLink.dataset.currentPmDossier='1';group.append(projectLink);}
   if(projectLink){projectLink.style.display=current?'block':'none';if(current)projectLink.href='/app/pm/projects/'+current[1];}
  }
  document.querySelectorAll('a[href^="/app/projects/"]').forEach(a=>{
   const match=a.getAttribute('href').match(/^\/app\/projects\/([^/?#]+)$/);
   if(match&&!a.parentElement.querySelector('[data-pm-dossier]')){const b=link('项目档案','/app/pm/projects/'+match[1]);b.dataset.pmDossier='1';a.after(b);}
  });
  // The legacy sidebar's 项目 entry used to land on the old project page.
  // Route it to the first current dossier so users open the new project view
  // directly; keep the legacy list as a fallback when the workspace is empty.
  document.querySelectorAll('aside a, nav a').forEach(a=>{
   if(a.textContent.trim()!=='项目'||a.dataset.pmProjectRoute==='1')return;
   a.dataset.pmProjectRoute='1';
   a.addEventListener('click',async event=>{
    event.preventDefault();
    event.stopImmediatePropagation();
    try{
     const response=await fetch('/v1/web/workspace/projects',{credentials:'include'});
     if(!response.ok)throw new Error('projects unavailable');
     const payload=await response.json();const first=(payload.items||[])[0];
     if(first?.id){location.href='/app/pm/projects/'+encodeURIComponent(first.id);return;}
    }catch{}
    location.href=a.getAttribute('href')||'/app/projects';
   });
  });
 }
 new MutationObserver(connect).observe(document.body,{childList:true,subtree:true});connect();
})();
