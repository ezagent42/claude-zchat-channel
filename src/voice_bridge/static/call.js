// zchat voice — 前端通话逻辑
// 浏览器 mic → 16kHz mono PCM → WebSocket → voice_bridge
// WS binary frames → AudioContext 播放
//
// Phase 1 L0: 回音验证（说啥听啥 stub）；Phase 2 起会收到 agent TTS 回复。

(() => {
  const SAMPLE_RATE = 16000;
  const FRAME_MS = 100;                  // 100ms 一帧
  const FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS / 1000;

  const $status = document.getElementById("status");
  const $log = document.getElementById("log");
  const $btnConnect = document.getElementById("btn-connect");
  const $btnHangup = document.getElementById("btn-hangup");
  const $channel = document.getElementById("channel");
  const $customer = document.getElementById("customer");
  const $auth = document.getElementById("auth");

  // Parse query from current URL (dev mode passes channel/customer; jwt mode passes t)
  const params = new URLSearchParams(location.search);
  const channel = params.get("channel") || "";
  const customer = params.get("customer") || "";
  const token = params.get("t") || "";

  $channel.textContent = channel || "(从 token 解析)";
  $customer.textContent = customer || "(从 token 解析)";
  $auth.textContent = token ? "jwt" : (channel ? "dev" : "—");

  let ws = null;
  let audioCtx = null;
  let mediaStream = null;
  let processor = null;
  let source = null;
  let playbackCtx = null;
  let playbackScheduled = 0;

  const log = (msg) => {
    const ts = new Date().toISOString().slice(11, 19);
    $log.textContent += `[${ts}] ${msg}\n`;
    $log.scrollTop = $log.scrollHeight;
  };
  const setStatus = (text, cls = "") => {
    $status.textContent = text;
    $status.className = "status " + cls;
  };

  async function connect() {
    const wsUrl = buildWsUrl();
    setStatus("正在连接 voice_bridge ...");
    log(`ws connect → ${wsUrl}`);
    ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";

    ws.onopen = async () => {
      setStatus("已接通，正在拿麦克风权限", "connected");
      log("ws open");
      try {
        await startCapture();
        setStatus("已接通，开始通话", "connected");
        $btnHangup.disabled = false;
        $btnConnect.disabled = true;
      } catch (e) {
        setStatus("麦克风权限被拒绝：" + e.message, "error");
        log("getUserMedia error: " + e.message);
        ws.close();
      }
    };

    ws.onmessage = async (ev) => {
      if (ev.data instanceof ArrayBuffer) {
        // Binary frame = PCM s16le 16kHz mono audio chunk
        playPcmChunk(ev.data);
      } else {
        // Text frame = control message (json)
        try {
          const msg = JSON.parse(ev.data);
          log("ws ← " + JSON.stringify(msg));
        } catch (e) {
          log("ws text: " + ev.data);
        }
      }
    };

    ws.onerror = (ev) => {
      log("ws error");
    };

    ws.onclose = (ev) => {
      setStatus(`已挂断（code=${ev.code}${ev.reason ? " · " + ev.reason : ""}）`);
      log(`ws close code=${ev.code} reason="${ev.reason}"`);
      stopCapture();
      $btnHangup.disabled = true;
      $btnConnect.disabled = false;
      ws = null;
    };
  }

  function buildWsUrl() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    // 保留所有 query params 透传（channel/customer/t）
    return `${proto}://${location.host}/ws${location.search}`;
  }

  async function startCapture() {
    // 不开 echoCancellation / noiseSuppression：有些浏览器会把人声当
    // 回声/噪声压掉（幅度降到 ±30），ASR 识别失败。Volcengine server 侧
    // 自带 VAD 和降噪，这里只要原始音频。
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
        sampleRate: SAMPLE_RATE,
      },
    });
    // 有些浏览器（尤其 Firefox/Safari）不接受 AudioContext({sampleRate:16000})
    // 而会用系统默认 48000Hz。ASR 期待 16kHz，如果发 48k 会识别失败。
    // 所以：用原生 rate 创建，运行时降采样。
    audioCtx = new AudioContext();
    const actualRate = audioCtx.sampleRate;
    const downsampleFactor = actualRate / SAMPLE_RATE;  // 48000/16000 = 3
    log(`mic capture: context rate=${actualRate}Hz, target=${SAMPLE_RATE}Hz, factor=${downsampleFactor.toFixed(2)}`);
    source = audioCtx.createMediaStreamSource(mediaStream);

    // 用 ScriptProcessor (简单，兼容性好；AudioWorklet 更现代但代码翻倍)
    const bufferSize = 2048;
    processor = audioCtx.createScriptProcessor(bufferSize, 1, 1);
    let frameBuffer = new Int16Array(0);

    // 下采样（48kHz → 16kHz typically factor=3）。
    // 纯 decimate（取 every N-th 样本）会引入 aliasing，对 ASR 是灾难
    // （Volcengine 会收到类似噪声的信号，返回空文本 partial/final）。
    // 改用 box-filter 平均 N 个样本：简单但至少把 > 8kHz 高频压下去。
    function downsample(input, factor) {
      if (Math.abs(factor - 1) < 0.01) return input;
      const n = Math.round(factor);
      const outLen = Math.floor(input.length / n);
      const out = new Float32Array(outLen);
      for (let i = 0; i < outLen; i++) {
        let sum = 0;
        const base = i * n;
        for (let k = 0; k < n; k++) sum += input[base + k];
        out[i] = sum / n;
      }
      return out;
    }

    // VAD state for barge-in (Phase 5)
    // 简单 RMS 能量阈值：超阈值 > HOLD_MS 视为开口；低阈值 > SILENCE_MS 视为停口
    const VAD_RMS_THRESHOLD = 0.025;  // -32 dBFS 附近
    const VAD_SPEECH_HOLD_MS = 120;   // 持续 120ms 超阈值才触发 speech_start
    const VAD_SILENCE_HOLD_MS = 600;  // 持续 600ms 静音才触发 speech_end
    let vadSpeechAccumMs = 0;
    let vadSilenceAccumMs = 0;
    let vadSpeaking = false;

    function frameRMS(f32) {
      let sum = 0;
      for (let i = 0; i < f32.length; i++) sum += f32[i] * f32[i];
      return Math.sqrt(sum / f32.length);
    }

    function vadUpdate(f32, durMs) {
      const rms = frameRMS(f32);
      const loud = rms > VAD_RMS_THRESHOLD;
      if (loud) {
        vadSpeechAccumMs += durMs;
        vadSilenceAccumMs = 0;
        if (!vadSpeaking && vadSpeechAccumMs >= VAD_SPEECH_HOLD_MS) {
          vadSpeaking = true;
          onSpeechStart();
        }
      } else {
        vadSilenceAccumMs += durMs;
        vadSpeechAccumMs = 0;
        if (vadSpeaking && vadSilenceAccumMs >= VAD_SILENCE_HOLD_MS) {
          vadSpeaking = false;
          onSpeechEnd();
        }
      }
    }

    function onSpeechStart() {
      log("VAD: speech_start");
      setStatus("🎤 识别中...", "connected");
      // 1. Cancel local TTS playback (instant; no server round-trip needed)
      if (playbackCtx) {
        try { playbackCtx.close(); } catch (e) {}
        playbackCtx = null;
        playbackScheduled = 0;
      }
      // 2. Tell server to stop TTS + notify agent
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: "speech_start" }));
      }
    }

    function onSpeechEnd() {
      log("VAD: speech_end");
      setStatus("已接通，开始通话", "connected");
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: "speech_end" }));
      }
    }

    // 诊断：每 2 秒汇报一次麦实际 peak / RMS（让用户知道音频有没有进来）
    let peakBucket = 0;
    let rmsSqBucket = 0;
    let samplesBucket = 0;
    let lastPeakLog = performance.now();

    processor.onaudioprocess = (ev) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const rawInput = ev.inputBuffer.getChannelData(0);  // float32 -1..1 @ actualRate
      // 下采样到 16kHz
      const input = downsample(rawInput, downsampleFactor);
      const durMs = (input.length / SAMPLE_RATE) * 1000;
      // 监控 peak / RMS（基于原始 float32）
      for (let i = 0; i < rawInput.length; i++) {
        const a = Math.abs(rawInput[i]);
        if (a > peakBucket) peakBucket = a;
        rmsSqBucket += rawInput[i] * rawInput[i];
      }
      samplesBucket += rawInput.length;
      const now = performance.now();
      if (now - lastPeakLog > 2000 && samplesBucket > 0) {
        const rms = Math.sqrt(rmsSqBucket / samplesBucket);
        log(`mic level: peak=${peakBucket.toFixed(4)} rms=${rms.toFixed(5)} (正常说话 peak>0.1, rms>0.02)`);
        peakBucket = 0; rmsSqBucket = 0; samplesBucket = 0;
        lastPeakLog = now;
      }
      // VAD on downsampled float32
      vadUpdate(input, durMs);
      // Forward as PCM s16 @ 16kHz
      const pcm16 = new Int16Array(input.length);
      for (let i = 0; i < input.length; i++) {
        const v = Math.max(-1, Math.min(1, input[i]));
        pcm16[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
      }
      const combined = new Int16Array(frameBuffer.length + pcm16.length);
      combined.set(frameBuffer, 0);
      combined.set(pcm16, frameBuffer.length);
      let offset = 0;
      while (combined.length - offset >= FRAME_SAMPLES) {
        const frame = combined.subarray(offset, offset + FRAME_SAMPLES);
        ws.send(frame.buffer.slice(frame.byteOffset, frame.byteOffset + frame.byteLength));
        offset += FRAME_SAMPLES;
      }
      frameBuffer = combined.slice(offset);
    };

    source.connect(processor);
    processor.connect(audioCtx.destination);
    log(`mic capturing @${SAMPLE_RATE}Hz, frame ${FRAME_MS}ms`);
  }

  function stopCapture() {
    if (processor) { try { processor.disconnect(); } catch (e) {} processor = null; }
    if (source)    { try { source.disconnect();    } catch (e) {} source = null; }
    if (audioCtx)  { try { audioCtx.close();       } catch (e) {} audioCtx = null; }
    if (mediaStream) {
      for (const t of mediaStream.getTracks()) t.stop();
      mediaStream = null;
    }
  }

  function playPcmChunk(buf) {
    if (!playbackCtx) {
      playbackCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
      playbackScheduled = playbackCtx.currentTime;
    }
    const pcm16 = new Int16Array(buf);
    const f32 = new Float32Array(pcm16.length);
    for (let i = 0; i < pcm16.length; i++) f32[i] = pcm16[i] / 0x8000;
    const ab = playbackCtx.createBuffer(1, f32.length, SAMPLE_RATE);
    ab.getChannelData(0).set(f32);
    const src = playbackCtx.createBufferSource();
    src.buffer = ab;
    src.connect(playbackCtx.destination);
    const start = Math.max(playbackCtx.currentTime, playbackScheduled);
    src.start(start);
    playbackScheduled = start + ab.duration;
  }

  function hangup() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.close(1000, "user hangup");
    }
    stopCapture();
  }

  $btnConnect.addEventListener("click", connect);
  $btnHangup.addEventListener("click", hangup);
})();
