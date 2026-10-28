(() => {
  const stage=document.querySelector('#stage');
  const box=document.createElement('div');box.id='stage-caption';box.hidden=true;
  box.setAttribute('aria-live','polite');stage.appendChild(box);
  const style=document.createElement('style');
  style.textContent=`#stage-caption{position:absolute;z-index:8;left:50%;bottom:54px;transform:translateX(-50%);width:1240px;min-height:70px;padding:16px 28px;border-radius:18px;background:rgba(5,22,42,.78);border:1px solid rgba(170,225,245,.3);box-shadow:0 5px 24px #00102033;color:#f3fbff;text-shadow:0 2px 3px #0008;font:500 36px/1.5 "Microsoft YaHei",sans-serif;letter-spacing:1px;white-space:pre-wrap;overflow-wrap:anywhere;text-align:center;pointer-events:none}#stage-caption[hidden]{display:none}#stage[data-layout="clean-portrait"] #stage-caption{width:920px;bottom:145px;font-size:40px;padding:18px 26px}`;
  document.head.appendChild(style);
  let timer=0,hideTimer=0,lastId='',generation=0;
  const params=new URLSearchParams(location.search);
  window.__stageCaption={state:'idle'};
  function show(text,id){
    clearTimeout(timer);clearTimeout(hideTimer);generation++;
    const own=generation;
    // textContent 防止台词中的 HTML 被执行；每页最多两行左右，不堆成长面板。
    const chars=Array.from(String(text).slice(0,1000));
    const count=stage.dataset.layout==='clean-portrait'?36:60;
    let index=0,page='';box.textContent='';box.hidden=!chars.length;
    window.__stageCaption={state:'typing',id,text:String(text)};
    function step(){
      if(own!==generation)return;
      if(index>=chars.length){window.__stageCaption.state='holding';hideTimer=setTimeout(()=>{box.hidden=true;window.__stageCaption.state='idle';},6500);return;}
      if(index && index%count===0){page='';timer=setTimeout(step,900);index++;page=chars[index-1]||'';box.textContent=page;return;}
      page+=chars[index++];box.textContent=page;timer=setTimeout(step,55);
    }
    step();
  }
  if(params.get('captions')==='0')return;
  if(params.get('caption_demo')==='1'){
    const demo=()=>show('你好呀，欢迎来到海底大厅。希望你今天也有一件开心的小事。','DEMO_ONLY');
    setTimeout(demo,650);setInterval(demo,12000);return;
  }
  async function poll(){
    try{
      const response=await fetch('http://127.0.0.1:5500/state',{cache:'no-store',signal:AbortSignal.timeout(800)});
      if(!response.ok)throw new Error('caption service unavailable');
      const data=await response.json(),id=String(data.id)+':'+String(data.at);
      const age=Date.now()/1000-Number(data.at);
      if(data.text && age>=-2 && age<45 && id!==lastId){lastId=id;show(data.text,id);}
    }catch{/* 字幕离线不影响角色渲染和语音。 */}
    setTimeout(poll,250);
  }
  poll();
})();
