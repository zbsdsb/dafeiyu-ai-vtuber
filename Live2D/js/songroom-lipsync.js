(() => {
  // 信号来自核心进程的 Windows 播放会话，不申请麦克风，也不播放第二份音频。
  let target=0,current=0,sourceTime=0,lastFrame=performance.now();
  window.__songroomLipSync=0;
  window.__songroomLipStatus={ready:false,sourcePid:0,sessionCount:0,raw:0};
  async function poll(){
    try{
      const response=await fetch('../out/songroom/mouth.json?_='+Date.now(),{cache:'no-store',signal:AbortSignal.timeout(1000)});
      if(!response.ok)throw new Error('mouth signal unavailable');
      const data=await response.json();
      const fresh=Date.now()/1000-Number(data.updated_at)<1.5;
      const raw=Math.max(0,Math.min(1,Number(data.peak)||0));
      const ready=fresh&&Number(data.source_pid)>0&&Number(data.session_count)>0;
      sourceTime=ready?Number(data.updated_at):0;
      target=ready?Math.min(1,Math.pow(Math.max(0,raw-.012)*2.8,.68)):0;
      window.__songroomLipStatus={ready,sourcePid:Number(data.source_pid)||0,sessionCount:Number(data.session_count)||0,raw};
    }catch{
      target=0;sourceTime=0;window.__songroomLipStatus.ready=false;
    }finally{setTimeout(poll,65);}
  }
  function tick(now){
    if(Date.now()/1000-sourceTime>1.5)target=0;
    const dt=Math.min(.1,(now-lastFrame)/1000);lastFrame=now;
    current+=(target-current)*Math.min(1,dt*(target>current?24:15));
    if(current<.003)current=0;
    window.__songroomLipSync=Math.max(0,Math.min(1,current));
    requestAnimationFrame(tick);
  }
  poll();requestAnimationFrame(tick);
})();
