// Barge-in gate: decides, on the phone itself, when the policyholder is really
// talking over the agent. Pure logic (no audio APIs), so it can be tested with Node.
//
// While the agent speaks, the microphone is "guarded": the page sends silence to the
// model, so the agent's own voice coming back through the loudspeaker (echo) or a
// small noise can never cut it off. The gate opens only for sustained speech that is
// clearly louder than the echo level learned at the start of each reply.
// If the model does not confirm the interruption, reject() closes the gate again,
// raises the bar to that sound's level and waits before it can fire again, so one
// noise cannot cause a stop-start-stop stammer.
class BargeIn {
  constructor(o = {}) {
    this.startMs = o.startMs ?? 320;            // sustained speech needed (about two syllables)
    this.gapMs = o.gapMs ?? 130;                // a pause longer than this inside the sound resets the count
    this.warmupMs = o.warmupMs ?? 600;          // first part of each reply: learn the echo, never fire
    this.factor = o.factor ?? 3.0;              // x room background
    this.echoRatio = o.echoRatio ?? 2.5;        // x the agent's own echo
    this.idleFloor = o.idleFloor ?? 0.012;
    this.speakingFloor = o.speakingFloor ?? 0.05;
    this.cooldownMs = o.cooldownMs ?? 1500;
    this.noise = 0.005;
    this.echo = 0.01;                           // kept across replies: the speaker/room do not change
    this.state = "idle";                        // idle (agent silent) | guarding | open (user barged in)
    this.reset();
  }

  reset() { this.voicedMs = 0; this.quietMs = 0; this.peak = 0; this.speakingMs = 0; this.cooldown = 0; }

  threshold() {
    return Math.max(this.speakingFloor, this.echo * this.echoRatio, this.noise * this.factor * 2);
  }

  // Send the real microphone signal to the model? (false = send silence)
  get micOpen() { return this.state !== "guarding"; }

  // rms: loudness of this mic chunk (0..1); ms: chunk length; agentSpeaking: agent audio is playing.
  // Returns "barge" once when the user starts talking over the agent, otherwise null.
  update(rms, ms, agentSpeaking) {
    if (!agentSpeaking) {
      // Background level: falls fast in quiet moments, rises only slowly (~20 s).
      this.noise += (rms < this.noise ? 0.1 : 0.002) * (rms - this.noise);
      this.state = "idle";
      this.reset();
      return null;
    }
    if (this.state === "idle") this.state = "guarding";
    if (this.state === "open") return null;
    this.speakingMs += ms;
    if (this.speakingMs <= this.warmupMs) {          // learn how loud the agent's echo is here
      this.echo += 0.15 * (rms - this.echo);
      return null;
    }
    if (this.cooldown > 0) this.cooldown -= ms;
    if (rms > this.threshold()) {
      this.voicedMs += ms; this.quietMs = 0; this.peak = Math.max(this.peak, rms);
    } else {
      this.quietMs += ms;
      if (this.quietMs > this.gapMs) { this.voicedMs = 0; this.peak = 0; }
      this.echo += 0.03 * (rms - this.echo);         // keep tracking the echo from non-speech frames
    }
    if (this.cooldown <= 0 && this.voicedMs >= this.startMs) { this.state = "open"; return "barge"; }
    return null;
  }

  // The model did not confirm the interruption: it was not the user talking.
  reject() {
    this.state = "guarding";
    this.cooldown = this.cooldownMs;
    this.echo = Math.max(this.echo, this.peak / this.echoRatio);   // that level is now "not speech"
    this.voicedMs = 0; this.peak = 0;
  }
}

function rmsOf(samples) {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / Math.max(1, samples.length));
}

if (typeof module !== "undefined") module.exports = { BargeIn, rmsOf };
