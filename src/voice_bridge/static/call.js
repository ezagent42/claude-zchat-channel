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
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, sampleRate: SAMPLE_RATE },
    });
    audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
    source = audioCtx.createMediaStreamSource(mediaStream);

    // 用 ScriptProcessor (简单，兼容性好；AudioWorklet 更现代但代码翻倍)
    const bufferSize = 2048;
    processor = audioCtx.createScriptProcessor(bufferSize, 1, 1);
    let frameBuffer = new Int16Array(0);

    processor.onaudioprocess = (ev) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const input = ev.inputBuffer.getChannelData(0);  // float32 -1..1
      const pcm16 = new Int16Array(input.length);
      for (let i = 0; i < input.length; i++) {
        const v = Math.max(-1, Math.min(1, input[i]));
        pcm16[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
      }
      // Accumulate + emit frames of FRAME_SAMPLES
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
