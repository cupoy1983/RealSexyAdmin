() => {
  const offers=[], prices=[], props=[], variants=[]; let weight=null;
  const expectedId=location.pathname.match(/\/offer\/(\d+)\.html/)?.[1];
  const seen=new WeakSet(); let budget=12000;
  const scalar=x=>typeof x==='string'||typeof x==='number';
  const pick=(x,keys)=>Object.fromEntries(keys.filter(k=>scalar(x?.[k])).map(k=>[k,String(x[k]).slice(0,500)]));
  function walk(x,depth=0){
    if(!x||typeof x!=='object'||depth>10||budget--<=0||seen.has(x))return;seen.add(x);
    if(x.offerInfoModel&&typeof x.offerInfoModel==='object'){
      if(x.offerInfoModel.offerId&&String(x.offerInfoModel.offerId)!==expectedId)return;
      offers.push(pick(x.offerInfoModel,['offerId','subject','title','skuWeight']));
    }
    for(const key of ['currentPrices','skuRangePrices','rangePrices'])if(Array.isArray(x[key]))for(const p of x[key].slice(0,50))prices.push({...pick(p,['beginAmount','beginNumber','startQuantity','price','discountPrice']),source:key});
    if(Array.isArray(x.skuProps))for(const p of x.skuProps.slice(0,30)){
      const values=p.value||p.values||[];
      props.push({name:String(p.prop||p.name||'').slice(0,100),values:Array.isArray(values)?values.slice(0,100).map(v=>pick(v,['name','label','vid','imageUrl'])):[]});
    }
    if(x.skuMap&&typeof x.skuMap==='object')for(const [key,v] of Object.entries(x.skuMap).slice(0,1000))variants.push({spec_key:key.slice(0,500),...pick(v,['skuId','price','discountPrice','canBookCount','skuWeight'])});
    if(scalar(x.skuWeight))weight=String(x.skuWeight).slice(0,100);
    for(const [k,v] of Object.entries(x))if(!/cookie|token|password|account|member|user|recommend|related|similar/i.test(k))walk(v,depth+1);
  }
  for(const name of ['__INIT_DATA','__INITIAL_STATE','__PRELOADED_STATE','__GLOBAL_DATA','__GLOBAL_DATA__','__NEXT_DATA__','__ICE_APP_CONTEXT__','runParams','offerInfoModel']){
    try{const x=window[name];if(name==='offerInfoModel'&&x)offers.push(pick(x,['offerId','subject','title','skuWeight']));walk(x)}catch{}
  }
  for(const s of document.querySelectorAll('script[type="application/json"],script[type="application/ld+json"]')){
    if(s.textContent.length>2000000)continue;
    try{const x=JSON.parse(s.textContent);walk(x);if(x['@type']==='Product')offers.push({title:x.name,offerId:x.productID})}catch{}
  }
  // Read JSON literals only, including module-local hydration data; never eval script text.
  function literal(text,start){
    let depth=0,quote=null,escape=false;
    for(let i=start;i<Math.min(text.length,start+1000000);i++){
      const c=text[i];
      if(quote){if(escape)escape=false;else if(c==='\\')escape=true;else if(c===quote)quote=null;continue}
      if(c==='"'||c==="'"){quote=c;continue}
      if(c==='{'||c==='[')depth++;
      if(c==='}'||c===']'){if(--depth===0)return JSON.parse(text.slice(start,i+1))}
    }
    throw Error('incomplete JSON');
  }
  for(const s of Array.from(document.scripts).slice(0,60)){
    const text=s.textContent;if(text.length>2000000)continue;
    const match=/"offerInfoModel"\s*:\s*(\{)/.exec(text);if(!match)continue;
    try{
      const offer=literal(text,match.index+match[0].length-1);
      if(String(offer.offerId)!==expectedId)continue;
      walk({offerInfoModel:offer});
      for(const key of ['currentPrices','skuRangePrices','skuProps','skuMap']){
        const re=new RegExp('"'+key+'"\\s*:\\s*([\\[{])');const found=re.exec(text);
        if(found)try{walk({[key]:literal(text,found.index+found[0].length-1)})}catch{}
      }
      if(offer.skuWeight!=null)weight=String(offer.skuWeight).slice(0,100);
    }catch{}
  }
  const meta=k=>document.querySelector(k)?.getAttribute('content')||null;
  const title=meta('meta[property="og:title"]')||document.querySelector('h1')?.textContent||document.title;
  const image=meta('meta[property="og:image"]');
  const images=[image,...Array.from(document.querySelectorAll('[class*="gallery"] img,[class*="detail-gallery"] img')).map(e=>e.currentSrc||e.src)].filter(Boolean).slice(0,100);
  return {title:String(title||'').trim().slice(0,1000),offers,prices,properties:props,variants,weight,images};
}
