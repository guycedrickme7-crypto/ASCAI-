const CACHE='ascai-v6';
const FILES=['./','./index.html','./manifest.json','./config.js','./tarifs.json','./prix-officiels.json','./icon-192.png','./icon-512.png','./icon-maskable-512.png'];
self.addEventListener('install',e=>{
  e.waitUntil(caches.open(CACHE).then(c=>Promise.all(FILES.map(f=>c.add(f).catch(()=>{})))));
  self.skipWaiting();
});
self.addEventListener('activate',e=>{
  e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==CACHE).map(x=>caches.delete(x)))).then(()=>self.clients.claim()));
});
self.addEventListener('fetch',e=>{
  const r=e.request, u=new URL(r.url);
  if(r.method!=='GET'||!/^https?:$/.test(u.protocol))return;
  // Les échanges avec la base de données (données privées) ne passent jamais par le cache.
  if(u.origin!==self.location.origin&&!/(^|\.)(googleapis|gstatic)\.com$/.test(u.hostname))return;
  if(r.headers.has('authorization')||r.headers.has('apikey'))return;
  e.respondWith(
    fetch(r).then(res=>{
      if(res&&(res.ok||res.type==='opaque')){const c=res.clone();caches.open(CACHE).then(x=>x.put(r,c));}
      return res;
    }).catch(()=>caches.match(r,{ignoreSearch:true}).then(m=>m||(r.mode==='navigate'?caches.match('./index.html'):Response.error())))
  );
});
