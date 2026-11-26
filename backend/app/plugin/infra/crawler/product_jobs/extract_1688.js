() => {
  const offers=[], prices=[], props=[], variants=[]; let weight=null;
  const expectedId=location.pathname.match(/\/offer\/(\d+)\.html/)?.[1];
  const seen=new WeakSet(); let budget=12000;
  const scalar=x=>typeof x==='string'||typeof x==='number';
  const pick=(x,keys)=>Object.fromEntries(keys.filter(k=>scalar(x?.[k])).map(k=>[k,String(x[k]).slice(0,500)]));
  // Module detail template: only read named product modules after identity
  // verification. Do not recursively traverse shop/recommendation modules.
  const modules=window.context?.result?.data;
  const gallery=modules?.gallery?.fields;
  if(gallery&&String(gallery.offerId)===expectedId&&
      (window.FE_GLOBALS?.offerId==null||String(window.FE_GLOBALS.offerId)===expectedId)){
    const data=modules.Root?.fields?.dataJson||{};
    if(data.offerId!=null&&String(data.offerId)!==expectedId)return {title:'',offers:[],prices:[],properties:[],variants:[],images:[],weight:null};
    const title=modules.productTitle?.fields?.title||gallery.subject||'';
    const pm=modules.mainPrice?.fields?.priceModel||data.priceModel||{};
    const model=pm.priceDisplayType||modules.mainPrice?.fields?.originPriceType;
    const current=Array.isArray(pm.currentPrices)?pm.currentPrices:[];
    const sku=data.skuModel||{};
    const properties=(sku.skuProps||[]).slice(0,30).map(p=>({name:String(p.prop||p.name||'').slice(0,100),values:(p.value||p.values||[]).slice(0,100).map(v=>pick(v,['name','label','vid','imageUrl']))}));
    const pack=modules.productPackInfo?.fields||{};
    const weights=pack.pieceWeightScale?.pieceWeightScaleInfo||[];
    const weightColumn=(pack.pieceWeightScale?.columnList||[]).find(c=>c.name==='weight');
    const weightUnit=/kg|千克|公斤/i.test(weightColumn?.label||'')?'kg':/\(g\)|（g）|克/.test(weightColumn?.label||'')?'g':null;
    const variants=Object.entries(sku.skuInfoMap||sku.skuMap||{}).slice(0,1000).map(([key,v])=>{
      const row=weights.find(w=>w.skuId!=null&&String(w.skuId)===String(v.skuId));
      return {spec_key:key.replace(/&gt;/g,'>').slice(0,500),...pick(v,['skuId','price','discountPrice','canBookCount','skuWeight']),
        ...(row&&Number(row.weight)>0?{skuWeight:String(row.weight),weight_unit:weightUnit}:{})};
    });
    const amounts=current.map(p=>String(p.price)).filter(v=>/^\d+(?:\.\d+)?$/.test(v)).sort((a,b)=>Number(a)-Number(b));
    const images=(gallery.mainImage||gallery.offerImgList||[]).filter(v=>typeof v==='string').slice(0,100);
    return {title:'',offers:[{offerId:expectedId,title:String(title).slice(0,1000)}],
      prices:model==='rangePrice'?current.slice(0,50).map(p=>({...pick(p,['beginAmount','price']),source:'context.mainPrice.currentPrices'})):[],
      price_range:model==='skuPrice'&&amounts.length?{min:amounts[0],max:amounts.at(-1)}:null,
      price_type:model||null,properties,variants,images,image_source:images.length?'page_data':null,
      weight:Number(pack.unitWeight)>0?String(pack.unitWeight):null,weight_unit:Number(pack.unitWeight)>0?'kg':null,
      parser:'1688-module-detail-v1'};
  }
  function walk(x,depth=0,owned=false){
    if(!x||typeof x!=='object'||depth>10||budget--<=0||seen.has(x))return;seen.add(x);
    const identity=x.offerInfoModel?.offerId??x.offerId;
    if(identity!=null&&String(identity)!==expectedId)return;
    owned=owned||String(identity)===expectedId;
    if(x.offerInfoModel&&typeof x.offerInfoModel==='object'){
      if(x.offerInfoModel.offerId&&String(x.offerInfoModel.offerId)!==expectedId)return;
      offers.push(pick(x.offerInfoModel,['offerId','subject','title','offerTitle','skuWeight']));
    }
    if(owned){
      if(String(x.offerId)===expectedId&&(x.subject||x.title||x.offerTitle))offers.push(pick(x,['offerId','subject','title','offerTitle','skuWeight']));
      for(const key of ['currentPrices','skuRangePrices','rangePrices'])if(Array.isArray(x[key]))for(const p of x[key].slice(0,50))prices.push({...pick(p,['beginAmount','beginNumber','startQuantity','price','discountPrice']),source:key});
      if(Array.isArray(x.skuProps))for(const p of x.skuProps.slice(0,30)){
        const values=p.value||p.values||[];
        props.push({name:String(p.prop||p.name||'').slice(0,100),values:Array.isArray(values)?values.slice(0,100).map(v=>pick(v,['name','label','vid','imageUrl'])):[]});
      }
      if(x.skuMap&&typeof x.skuMap==='object')for(const [key,v] of Object.entries(x.skuMap).slice(0,1000))variants.push({spec_key:key.slice(0,500),...pick(v,['skuId','price','discountPrice','canBookCount','skuWeight'])});
      if(scalar(x.skuWeight))weight=String(x.skuWeight).slice(0,100);
    }
    for(const [k,v] of Object.entries(x))if(!/cookie|token|password|account|member|user|recommend|related|similar/i.test(k))walk(v,depth+1,owned);
  }
  for(const name of ['__INIT_DATA','__INITIAL_STATE','__PRELOADED_STATE','__GLOBAL_DATA','__GLOBAL_DATA__','__NEXT_DATA__','__ICE_APP_CONTEXT__','runParams','offerInfoModel']){
    try{const x=window[name];walk(x)}catch{}
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
    for(const match of text.matchAll(/"offerInfoModel"\s*:\s*(\{)/g)){
    try{
      const offer=literal(text,match.index+match[0].length-1);
      if(String(offer.offerId)!==expectedId)continue;
      // Parse an enclosing JSON object so sibling models keep their offer scope.
      // Never take the first price/SKU literal elsewhere in the script.
      let enclosed=false;
      const starts=[];
      for(let i=match.index-1;i>=0&&starts.length<100;i--)if(text[i]==='{')starts.push(i);
      for(const start of starts){
        try{const root=literal(text,start);
          if(String(root.offerInfoModel?.offerId)===expectedId){walk(root);enclosed=true;break}
        }catch{}
      }
      if(!enclosed)walk(offer);
    }catch{}
    }
  }
  const meta=k=>document.querySelector(k)?.getAttribute('content')||null;
  const title=meta('meta[property="og:title"]')||'';
  const image=meta('meta[property="og:image"]');
  const images=[image,...Array.from(document.querySelectorAll('[class*="gallery"] img,[class*="detail-gallery"] img')).map(e=>e.currentSrc||e.src)].filter(Boolean).slice(0,100);
  return {title:String(title||'').trim().slice(0,1000),offers,prices,properties:props,variants,weight,images};
}
