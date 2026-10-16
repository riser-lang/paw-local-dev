// Extend the existing sidebar without changing the compiled legacy application.
(()=>{
 function link(text,href){const a=document.createElement('a');a.textContent=text;a.href=href;a.style.cssText='display:block;padding:8px 16px;color:inherit;font:inherit;text-decoration:none';return a;}
 function connect(){
  const sidebar=document.querySelector('aside nav')||document.querySelector('aside')||document.querySelector('nav');
  if(sidebar&&!document.getElementById('paw-pm-navigation')){
   const group=document.createElement('div');group.id='paw-pm-navigation';group.setAttribute('aria-label','项目管理');
   group.append(link('驾驶舱','/app/pm/today'),link('资料去向','/app/pm/receipts'));sidebar.append(group);
  }
  document.querySelectorAll('a[href^="/app/projects/"]').forEach(a=>{
   const match=a.getAttribute('href').match(/^\/app\/projects\/([^/?#]+)$/);
   if(match&&!a.parentElement.querySelector('[data-pm-dossier]')){const b=link('项目档案','/app/pm/projects/'+match[1]);b.dataset.pmDossier='1';a.after(b);}
  });
 }
 new MutationObserver(connect).observe(document.body,{childList:true,subtree:true});connect();
})();
