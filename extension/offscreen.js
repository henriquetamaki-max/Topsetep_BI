// offscreen.js - Reprodutor de áudio no offscreen document

console.log('🔊 Offscreen document carregado');

// Criar elemento de áudio global
let audio = null;
let isAudioReady = false;

// Inicializar áudio
function initAudio() {
  if (audio) return;

  try {
    audio = new Audio(chrome.runtime.getURL('sounds/alert.mp3'));
    audio.volume = 0.8;
    audio.preload = 'auto';

    // Eventos para debug
    audio.addEventListener('loadeddata', () => {
      console.log('✓ Áudio carregado e pronto');
      isAudioReady = true;
    });

    audio.addEventListener('error', (e) => {
      console.error('❌ Erro ao carregar áudio:', e);
      isAudioReady = false;
    });

    audio.addEventListener('play', () => {
      console.log('▶️ Áudio começou a tocar');
    });

    audio.addEventListener('ended', () => {
      console.log('⏹️ Áudio terminou');
    });

    // Carregar áudio
    audio.load();
  } catch (error) {
    console.error('❌ Erro ao inicializar áudio:', error);
  }
}

// Inicializar imediatamente
initAudio();

// Listener para mensagens do background
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log('📨 Offscreen recebeu mensagem:', message);

  if (message.type === 'PLAY_SOUND') {
    playSound();
    sendResponse({ success: true, ready: isAudioReady });
  } else if (message.type === 'STOP_SOUND') {
    stopSound();
    sendResponse({ success: true });
  } else if (message.type === 'INIT_AUDIO') {
    initAudio();
    sendResponse({ success: true, ready: isAudioReady });
  }

  return true; // Manter canal aberto
});

function playSound() {
  console.log('🔊 Tentando tocar som...');

  if (!audio) {
    console.warn('⚠️ Áudio não inicializado, inicializando agora...');
    initAudio();

    // Tentar tocar após inicialização
    setTimeout(() => playSound(), 500);
    return;
  }

  if (!isAudioReady) {
    console.warn('⚠️ Áudio ainda não está pronto');
    return;
  }

  try {
    // Resetar para o início
    audio.currentTime = 0;

    // Tentar tocar
    const playPromise = audio.play();

    if (playPromise !== undefined) {
      playPromise
        .then(() => {
          console.log('✅ Som tocado com SUCESSO!');
        })
        .catch((error) => {
          console.error('❌ Erro ao tocar som:', error);
        });
    }
  } catch (error) {
    console.error('❌ Exceção ao tocar som:', error);
  }
}

function stopSound() {
  if (!audio) return;

  try {
    audio.pause();
    audio.currentTime = 0;
    console.log('⏹️ Som parado');
  } catch (error) {
    console.error('❌ Erro ao parar som:', error);
  }
}

console.log('✓ Offscreen document pronto para tocar áudio');
