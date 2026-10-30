// Agent face: a 3D avatar whose lips follow the agent's voice. TalkingHead renders the head
// (three.js); HeadAudio reads mouth shapes from the sound itself, so it works with the live
// model's audio stream without any text or timing data. app.js plays the agent's voice into
// the avatar's audio graph (AgentAvatar.ready). If anything here fails (no WebGL, CDN not
// reachable) the page works exactly as before, just without the face.
const HEADAUDIO = "https://cdn.jsdelivr.net/gh/met4citizen/HeadAudio@d3af5f9ff86ab6b2b1913d411a4e1922ec101953/dist/";
// TalkingHead's sample avatar (Ready Player Me): CC BY-NC 4.0, non-commercial use only. For
// production, swap in your own GLB with a Mixamo rig plus ARKit and Oculus viseme blend shapes.
const AVATAR_URL = "https://cdn.jsdelivr.net/gh/met4citizen/TalkingHead@1.7.0/avatars/brunette.glb";
// HeadAudio's mouth shapes lag the sound by 50-100 ms; delaying the voice lines them up.
const LIPSYNC_DELAY_S = 0.08;
const PHONE = matchMedia("(max-width:760px)");
// Head and shoulders in the Conversation card; just the face in the phone's small bubble.
// cameraX: the model's idle stance puts her head ~5 cm right of centre.
const VIEW = () => (PHONE.matches ? "head" : "upper");
const FRAMING = { upper: { cameraDistance: -1.6, cameraY: -0.2, cameraX: -0.2 }, head: { cameraDistance: 0.6, cameraY: 0.5, cameraX: -0.2 } };

const face = document.querySelector("#agentFace");
const msg = face.querySelector(".face-msg");
let head = null;

function place() {
  // Computer: top of the Conversation card. Phone: a small bubble on the camera view.
  const stage = document.querySelector("#stage");
  const home = PHONE.matches ? stage : document.querySelector("#faceSlot");
  if (face.parentElement !== home) home.appendChild(face);
  stage.classList.toggle("has-face", PHONE.matches);
  head?.setView(VIEW(), FRAMING[VIEW()]);
}

async function load() {
  place();
  PHONE.addEventListener("change", place);
  const [{ TalkingHead }, { HeadAudio }] = await Promise.all([import("talkinghead"), import(HEADAUDIO + "headaudio.min.mjs")]);
  head = new TalkingHead(face, {
    ttsEndpoint: "N/A", lipsyncModules: [], cameraView: VIEW(), ...FRAMING[VIEW()], cameraRotateEnable: false,
    modelFPS: PHONE.matches ? 24 : 30, modelPixelRatio: Math.min(1, 2 / devicePixelRatio),  // spare the phone's GPU
  });
  await head.showAvatar({ url: AVATAR_URL, body: "F", avatarMood: "neutral" }, (ev) => {
    if (ev?.lengthComputable) msg.textContent = `Loading the agent… ${Math.round(ev.loaded / ev.total * 100)}%`;
  });
  place();  // the screen may have crossed the phone/computer width while the model loaded

  const ctx = head.audioCtx;
  await ctx.audioWorklet.addModule(HEADAUDIO + "headworklet.min.mjs");
  const lips = new HeadAudio(ctx);
  await lips.loadModel(HEADAUDIO + "model-en-mixed.bin");
  head.audioSpeechGainNode.connect(lips);
  const delay = ctx.createDelay(1);
  delay.delayTime.value = LIPSYNC_DELAY_S;
  head.audioSpeechGainNode.disconnect(head.audioReverbNode);
  head.audioSpeechGainNode.connect(delay).connect(head.audioReverbNode);
  lips.onvalue = (key, value) => { const m = head.mtAvatar[key]; if (m) Object.assign(m, { newvalue: value, needsUpdate: true }); };
  head.opt.update = lips.update.bind(lips);

  // Look at the policyholder when a new reply starts (not on every short pause), with a hand gesture
  // when the upper body is in view.
  let lastEnded = 0;
  lips.onstarted = () => {
    face.dataset.talking = "";
    if (Date.now() - lastEnded > 150) { head.lookAtCamera(500); if (!PHONE.matches) head.speakWithHands(); }
  };
  lips.onended = () => { lastEnded = Date.now(); delete face.dataset.talking; };

  face.dataset.state = "ready";
  return { ctx, input: head.audioAnalyzerNode };
}

window.AgentAvatar = {
  ready: load().catch((e) => {
    console.warn("Agent avatar unavailable:", e);
    face.hidden = true;
    document.querySelector("#faceSlot").hidden = true;
    document.querySelector("#stage").classList.remove("has-face");
    return null;
  }),
};
