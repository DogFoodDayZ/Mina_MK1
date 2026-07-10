const stateEndpoint = '/state';
const eventsEndpoint = '/events';

const avatarEl = document.getElementById('avatar');
const statusBarEl = document.getElementById('status-bar');
const layerMap = Object.fromEntries(
  Array.from(document.querySelectorAll('.avatar-layer')).map(el => [el.dataset.layer, el])
);

let stateData = {
  state: 'idle',
  emotion: 'neutral',
};
let talkFrame = 0;
let talkTimer = null;
let smirkFrame = 0;
let smirkTimer = null;

const statusCopy = {
  idle: 'Idle — ready',
  talk: 'Talking',
  smirk: 'Pay attention',
  annoyed: 'Not amused',
  neutral: 'Idle — ready',
};

function setLayerVisible(layerName) {
  Object.values(layerMap).forEach(el => el.classList.remove('visible'));
  const layer = layerMap[layerName];
  if (layer) {
    layer.classList.add('visible');
  }
}

function startTalkAnimation() {
  if (talkTimer) return;
  talkTimer = setInterval(() => {
    talkFrame = talkFrame === 0 ? 1 : 0;
    renderLayers();
  }, 650);
}

function stopTalkAnimation() {
  if (talkTimer) {
    clearInterval(talkTimer);
    talkTimer = null;
  }
  talkFrame = 0;
}

function startSmirkAnimation() {
  if (smirkTimer) return;
  smirkTimer = setInterval(() => {
    smirkFrame = smirkFrame === 0 ? 1 : 0;
    renderLayers();
  }, 900);
}

function stopSmirkAnimation() {
  if (smirkTimer) {
    clearInterval(smirkTimer);
    smirkTimer = null;
  }
  smirkFrame = 0;
}

function renderLayers() {
  let layerToShow = 'idle';

  if (stateData.state === 'talk') {
    layerToShow = talkFrame === 0 ? 'talk-1' : 'talk-2';
  } else if (stateData.emotion === 'smirk') {
    layerToShow = smirkFrame === 0 ? 'smirk-1' : 'smirk-2';
  } else if (stateData.emotion === 'annoyed') {
    layerToShow = 'annoyed-1';
  }

  setLayerVisible(layerToShow);
}

function updateAnimations() {
  if (stateData.state === 'talk') {
    startTalkAnimation();
  } else {
    stopTalkAnimation();
  }

  if (stateData.state !== 'talk' && stateData.emotion === 'smirk') {
    startSmirkAnimation();
  } else {
    stopSmirkAnimation();
  }
}

function updateStatusBar() {
  let mode = 'idle';
  let copy = statusCopy.neutral;

  if (stateData.state === 'talk') {
    mode = 'talk';
    copy = statusCopy.talk;
  } else if (stateData.emotion === 'smirk') {
    mode = 'smirk';
    copy = statusCopy.smirk;
  } else if (stateData.emotion === 'annoyed') {
    mode = 'annoyed';
    copy = statusCopy.annoyed;
  } else {
    copy = statusCopy.idle;
  }

  statusBarEl.dataset.mode = mode;
  statusBarEl.textContent = copy;
}

function applyState(newState) {
  stateData = {
    ...stateData,
    ...newState,
    emotion: newState.emotion || 'neutral',
    state: newState.state || 'idle',
  };
  avatarEl.dataset.updated = stateData.updatedAt || '';
  updateAnimations();
  renderLayers();
  updateStatusBar();
}

async function fetchState() {
  try {
    const res = await fetch(stateEndpoint);
    if (!res.ok) return;
    const data = await res.json();
    applyState(data);
  } catch (err) {
    console.warn('state fetch failed', err);
  }
}

function initSSE() {
  const source = new EventSource(eventsEndpoint);
  source.onmessage = event => {
    try {
      const data = JSON.parse(event.data);
      applyState(data);
    } catch (err) {
      console.warn('bad event payload', err);
    }
  };
  source.onerror = () => {
    console.warn('SSE connection lost, retrying via polling');
    source.close();
    setInterval(fetchState, 3000);
  };
}

renderLayers();
updateStatusBar();
initSSE();
fetchState();
