/* 舞台叠加层：思考气泡 + 鼓掌特效 + 思考姿态。
 *
 * 信号来源是 5500 的字幕服务（/state 轮询），不申请麦克风、不额外播放音频：
 *   thinking      —— 核心开始跑 LLM 时置位，出结果后清除，服务端 25 秒自动失效
 *   effect/seq    —— 一次性特效，前端按 seq 增量判断是否是新事件
 *
 * 姿态通过 window.__songroomPose 交给 bundle.songroom-lipsync.js 应用，
 * 模型没有 motions（Motions 为空），所以「思考」用歪头+眼球上翻+抬眉来表现。
 */
(() => {
  const API = 'http://127.0.0.1:5500/state';
  const POSES = { thinking: { angleZ: 13, eyeY: 0.85, eyeX: 0.3, browY: 0.9 } };
  const THINK_TEXTS = ['让我想想…', '本鱼在翻了…', '稍等，正在组织语言', '这个问题有点意思', '嗯……容我想想'];
  const CLAP_EMOJI = ['👏', '✨', '💙', '🐟', '🎉', '👏'];

  // 水印部件（cdi3.json 里的 Parts）：Part46「水印.png」、Part45「水印2.png」
  const stage = document.getElementById('stage');
  const model = document.getElementById('model-surface');
  if (!stage) return;

  const style = document.createElement('style');
  style.textContent = `
  .so-layer{position:absolute;inset:0;pointer-events:none;z-index:9;overflow:hidden}
  .so-bubble{position:absolute;opacity:0;transform:translateY(6px) scale(.92);
    transition:opacity .22s ease,transform .22s ease;
    max-width:min(300px,30%);padding:14px 18px 15px;border-radius:22px;
    background:linear-gradient(160deg,#fdfcf6 0%,#eef4fb 100%);
    border:3px solid #1d3a63;box-shadow:0 10px 24px rgba(6,26,52,.35), inset 0 0 0 2px rgba(255,255,255,.8);
    color:#16283f;font:600 26px/1.35 "Microsoft YaHei","PingFang SC",sans-serif;
    display:flex;align-items:center;gap:10px;white-space:normal;line-height:1.3}
  .so-bubble.on{opacity:1;transform:translateY(0) scale(1);animation:so-bob 2.4s ease-in-out infinite}
  .so-bubble::after{content:"";position:absolute;left:-16px;bottom:16px;width:0;height:0;
    border:12px solid transparent;border-right:18px solid #1d3a63;transform:rotate(8deg)}
  .so-bubble::before{content:"";position:absolute;left:-10px;bottom:19px;width:0;height:0;
    border:9px solid transparent;border-right:14px solid #f4f7fb;transform:rotate(8deg);z-index:1}
  .so-mind{font-size:30px;line-height:1}
  .so-dots{display:inline-flex;gap:5px;margin-left:2px}
  .so-dots i{width:8px;height:8px;border-radius:50%;background:#1d3a63;opacity:.25;animation:so-dot 1.15s infinite}
  .so-dots i:nth-child(2){animation-delay:.16s}.so-dots i:nth-child(3){animation-delay:.32s}
  @keyframes so-bob{0%,100%{transform:translateY(0) scale(1)}50%{transform:translateY(-5px) scale(1.01)}}
  @keyframes so-dot{0%,100%{opacity:.2}40%{opacity:1}}
  .so-fx{position:absolute;font-size:64px;line-height:1;animation:so-fly 3.6s ease-out forwards;will-change:transform,opacity}
  @keyframes so-fly{0%{opacity:0;transform:translateY(24px) scale(.5) rotate(0deg)}
    18%{opacity:1}100%{opacity:0;transform:translateY(-320px) scale(1.15) rotate(var(--rot))}}
  .so-clap{position:absolute;left:50%;top:27%;font-size:120px;line-height:1;
    transform:translate(-50%,-50%) scale(.3);opacity:0;animation:so-pop 3.2s ease-out forwards}
  @keyframes so-pop{0%{opacity:0;transform:translate(-50%,-50%) scale(.3) rotate(-8deg)}
    22%{opacity:1;transform:translate(-50%,-50%) scale(1.12) rotate(4deg)}
    70%{opacity:1;transform:translate(-50%,-50%) scale(1) rotate(0)}
    100%{opacity:0;transform:translate(-50%,-50%) scale(1.25)}}
  `;
  document.head.appendChild(style);

  const layer = document.createElement('div');
  layer.className = 'so-layer';
  stage.appendChild(layer);

  const bubble = document.createElement('div');
  bubble.className = 'so-bubble';
  bubble.innerHTML = '<span class="so-mind">💭</span><span class="so-text"></span>'
    + '<span class="so-dots"><i></i><i></i><i></i></span>';
  layer.appendChild(bubble);
  const bubbleText = bubble.querySelector('.so-text');

  // 气泡跟着角色头部走：用模型框的实测位置换算成 stage 内坐标，
  // 这样横屏/竖屏两套布局都不用各写一套坐标。
  function place() {
    if (!model) return;
    const s = stage.getBoundingClientRect();
    const r = model.getBoundingClientRect();
    const k = stage.offsetWidth ? s.width / stage.offsetWidth : 1;
    const left = (r.left - s.left) / k;
    const top = (r.top - s.top) / k;
    const w = r.width / k;
    const h = r.height / k;
    bubble.style.left = Math.round(left + w * 0.63) + 'px';
    bubble.style.top = Math.round(top + h * 0.02) + 'px';
  }


  // 手势库：模型没有 motions、也没有四肢参数，所以只能做头/身体/表情类动作。
  // 每条曲线是 [进度0~1, 值] 的关键点，用 easeInOut 插值后叠加到待机姿态上。
  const GESTURES = {
    nod:    { dur: 1.0, curves: { angleY: [[0, 0], [0.35, 7], [0.68, -1.5], [1, 0]] } },
    shake:  { dur: 1.2, curves: { angleX: [[0, 0], [0.2, 9], [0.45, -9], [0.7, 6], [1, 0]] } },
    tilt:   { dur: 1.8, curves: { angleZ: [[0, 0], [0.35, 11], [0.85, 9], [1, 0]],
                                  eyeX:   [[0, 0], [0.35, 0.5], [0.85, 0.45], [1, 0]] } },
    look:   { dur: 2.2, curves: { eyeX:   [[0, 0], [0.22, 0.7], [0.55, -0.7], [1, 0]],
                                  angleX: [[0, 0], [0.22, 4], [0.55, -4], [1, 0]] } },
    bounce: { dur: 0.9, curves: { bodyZ:  [[0, 0], [0.25, -6], [0.5, 3], [0.75, -1.5], [1, 0]],
                                  angleY: [[0, 0], [0.25, -3], [0.5, 2], [1, 0]] } },
    recoil: { dur: 0.8, curves: { bodyZ:  [[0, 0], [0.3, 9], [1, 0]],
                                  angleY: [[0, 0], [0.3, -6], [1, 0]],
                                  browY:  [[0, 0], [0.3, 0.9], [1, 0]] } },
  };

  let activeGesture = null;

  function easeInOut(t) {
    return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
  }

  function sampleCurve(points, progress) {
    for (let i = 0; i < points.length - 1; i += 1) {
      const [t0, v0] = points[i];
      const [t1, v1] = points[i + 1];
      if (progress <= t1) {
        const span = t1 - t0 || 1;
        const local = easeInOut(Math.min(1, Math.max(0, (progress - t0) / span)));
        return v0 + (v1 - v0) * local;
      }
    }
    return points[points.length - 1][1];
  }

  function playGesture(name) {
    const gesture = GESTURES[name];
    if (!gesture) return;
    activeGesture = { ...gesture, startedAt: performance.now() / 1000, name };
  }


  function gestureOffset(now) {
    if (!activeGesture) return null;
    const progress = (now - activeGesture.startedAt) / activeGesture.dur;
    if (progress >= 1) { activeGesture = null; return null; }
    const out = {};
    Object.entries(activeGesture.curves).forEach(([key, points]) => { out[key] = sampleCurve(points, progress); });
    return out;
  }

  let thinking = false;

  function setThinking(on) {
    bubble.classList.toggle('on', !!on);
    thinking = !!on;
    if (on) {
      bubbleText.textContent = THINK_TEXTS[Math.floor(Math.random() * THINK_TEXTS.length)];
      place();
    }
  }

  // 持续驱动姿态：待机轻摇 + 说话点头 + 思考歪头。
  // 模型没有 motions 也没有手臂/尾巴参数，能动只有头部、身体角度和眼眉，
  // 但头发和尾巴挂了物理，头一动就会自然飘。
  function poseLoop() {
    const t = performance.now() / 1000;
    const talking = (window.__songroomLipSync || 0) > 0.04;
    const pose = {
      angleZ: 3.2 * Math.sin(t * 0.52),
      angleY: 1.6 * Math.sin(t * 0.31),
      bodyZ: 2.4 * Math.sin(t * 0.37 + 0.8),
    };
    if (talking) {
      // 说话时轻轻点头，节奏跟着嘴动，幅度压小以免晕
      pose.angleY += 2.0 * Math.sin(t * 8.5);
      pose.bodyZ += 1.2 * Math.sin(t * 4.2);
    }
    if (thinking) {
      pose.angleZ += POSES.thinking.angleZ;
      pose.eyeY = POSES.thinking.eyeY;
      pose.eyeX = POSES.thinking.eyeX;
      pose.browY = POSES.thinking.browY;
    }
    const gesture = gestureOffset(t);
    if (gesture) {
      Object.entries(gesture).forEach(([key, value]) => { pose[key] = (pose[key] || 0) + value; });
    }
    window.__songroomPose = pose;
    requestAnimationFrame(poseLoop);
  }

  function playEffect(name) {
    if (name && name.indexOf('gesture:') === 0) {
      // 调试用：/effect?name=gesture:nod 可以单独触发某个手势
      playGesture(name.slice(8));
      return;
    }
    if (name === 'clap') {
      // 只做参数手势，不切表情：模型靠「13 水印消除」表情遮住围裙上的
      // 作者水印和署名，一旦切换到别的表情就会把那段字露到直播间里。
      playGesture('bounce');
      const big = document.createElement('div');
      big.className = 'so-clap';
      big.textContent = '👏';
      layer.appendChild(big);
      setTimeout(() => big.remove(), 3400);

      for (let i = 0; i < 14; i += 1) {
        const span = document.createElement('span');
        span.className = 'so-fx';
        span.textContent = CLAP_EMOJI[Math.floor(Math.random() * CLAP_EMOJI.length)];
        span.style.left = (8 + Math.random() * 84) + '%';
        span.style.top = (52 + Math.random() * 30) + '%';
        span.style.fontSize = (36 + Math.random() * 40).toFixed(0) + 'px';
        span.style.setProperty('--rot', (Math.random() * 60 - 30).toFixed(0) + 'deg');
        span.style.animationDelay = (Math.random() * 0.5).toFixed(2) + 's';
        layer.appendChild(span);
        setTimeout(() => span.remove(), 4200);
      }
    }
  }

  let lastEffectSeq = null;
  let lastThinking = null;

  async function poll() {
    try {
      const res = await fetch(`${API}?_=${Date.now()}`, { cache: 'no-store', signal: AbortSignal.timeout(1500) });
      const data = await res.json();

      if (data.thinking !== lastThinking) {
        lastThinking = data.thinking;
        setThinking(data.thinking);
      }

      const seq = Number(data.effect_seq) || 0;
      if (lastEffectSeq === null) {
        lastEffectSeq = seq;                      // 初次加载不补播历史事件
      } else if (seq > lastEffectSeq) {
        lastEffectSeq = seq;
        if (Date.now() / 1000 - Number(data.effect_at || 0) < 12) playEffect(data.effect);
      }
      // 空闲时随机来个小动作，别老一个姿势（20~45 秒一次）
      if (!window.__soNextGestureAt) window.__soNextGestureAt = Date.now() + 20000 + Math.random() * 25000;
      if (Date.now() > window.__soNextGestureAt && !thinking) {
        const pool = ['tilt', 'look', 'nod'];
        playGesture(pool[Math.floor(Math.random() * pool.length)]);
        window.__soNextGestureAt = Date.now() + 20000 + Math.random() * 25000;
      }
    } catch {
      if (lastThinking !== false) {
        lastThinking = false;
        setThinking(false);
      }
    } finally {
      setTimeout(poll, 300);
    }
  }

  addEventListener('resize', place);
  place();
  poseLoop();
  poll();
})();
