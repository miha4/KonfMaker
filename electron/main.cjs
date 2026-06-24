const { app, BrowserWindow, dialog } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const BACKEND_PORT = Number(process.env.KONFMAKER_BACKEND_PORT || 8765);
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
const ROOT_DIR = path.resolve(__dirname, '..');

let mainWindow = null;
let backendProcess = null;

function appResourcePath(...parts) {
  return app.isPackaged ? path.join(process.resourcesPath, ...parts) : path.join(ROOT_DIR, ...parts);
}

function fileExists(filePath) {
  try {
    return fs.existsSync(filePath);
  } catch {
    return false;
  }
}

function copyIfMissing(sourcePath, targetPath) {
  if (!fileExists(sourcePath) || fileExists(targetPath)) {
    return;
  }
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  fs.copyFileSync(sourcePath, targetPath);
}

function findDevPython() {
  const candidates = [
    process.env.PYTHON,
    path.join(ROOT_DIR, 'backend', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'),
    'python3',
    'python',
  ].filter(Boolean);

  return candidates[0];
}

function packagedEnginePath() {
  const binaryName = process.platform === 'win32' ? 'konfmaker-engine.exe' : 'konfmaker-engine';
  return appResourcePath('backend', binaryName);
}

function backendEnvironment() {
  const userDataDir = app.getPath('userData');
  const userConfigPath = path.join(userDataDir, 'user_configurations.json');
  const patternCachePath = path.join(userDataDir, 'pattern-cache', 'patterns.json');
  const workbookPath = appResourcePath('data', 'Konfiguracije OKZP.xlsx');
  const configCsvPath = appResourcePath('data', 'konfiguracije_okzp_obogateno_vlimiti.csv');

  copyIfMissing(appResourcePath('data', 'user_configurations.json'), userConfigPath);
  fs.mkdirSync(path.dirname(patternCachePath), { recursive: true });

  return {
    ...process.env,
    KONFMAKER_DESKTOP: '1',
    KONFMAKER_BACKEND_HOST: '127.0.0.1',
    KONFMAKER_BACKEND_PORT: String(BACKEND_PORT),
    KONFMAKER_CONFIG_LIBRARY_CSV: configCsvPath,
    KONFMAKER_CONFIG_WORKBOOK_XLSX: workbookPath,
    KONFMAKER_USER_CONFIG_LIBRARY_JSON: userConfigPath,
    KONFMAKER_PATTERN_CACHE_PATH: patternCachePath,
    PYTHONUNBUFFERED: '1',
  };
}

function startBackend() {
  const env = backendEnvironment();

  if (app.isPackaged) {
    const enginePath = packagedEnginePath();
    if (!fileExists(enginePath)) {
      throw new Error(`Zapakiran KonfMaker engine ni najden: ${enginePath}`);
    }
    backendProcess = spawn(enginePath, [], {
      cwd: path.dirname(enginePath),
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  } else {
    const python = findDevPython();
    backendProcess = spawn(
      python,
      ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)],
      {
        cwd: path.join(ROOT_DIR, 'backend'),
        env,
        stdio: ['ignore', 'pipe', 'pipe'],
      },
    );
  }

  backendProcess.stdout?.on('data', (chunk) => {
    process.stdout.write(`[konfmaker-engine] ${chunk}`);
  });
  backendProcess.stderr?.on('data', (chunk) => {
    process.stderr.write(`[konfmaker-engine] ${chunk}`);
  });
  backendProcess.on('exit', (code, signal) => {
    if (!app.isQuitting) {
      console.error(`KonfMaker engine se je ustavil. code=${code} signal=${signal}`);
    }
  });
}

function stopBackend() {
  if (!backendProcess || backendProcess.killed) {
    return;
  }
  backendProcess.kill();
  backendProcess = null;
}

function backendHealthCheck() {
  return new Promise((resolve) => {
    const request = http.get(`${BACKEND_URL}/api/health`, { timeout: 1000 }, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });
    request.on('error', () => resolve(false));
    request.on('timeout', () => {
      request.destroy();
      resolve(false);
    });
  });
}

async function waitForBackend(timeoutMs = 60000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (await backendHealthCheck()) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 350));
  }
  return false;
}

function startupHtml(title, detail) {
  return `data:text/html;charset=utf-8,${encodeURIComponent(`
    <!doctype html>
    <html lang="sl">
      <head>
        <meta charset="utf-8" />
        <title>KonfMaker</title>
        <style>
          body {
            margin: 0;
            min-height: 100vh;
            display: grid;
            place-items: center;
            background: #e7eef5;
            color: #15233a;
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          }
          main {
            width: min(720px, calc(100vw - 48px));
            padding: 36px;
            border-radius: 24px;
            background: #fff;
            box-shadow: 0 24px 70px rgba(28, 54, 82, 0.18);
          }
          p { color: #607188; font-size: 17px; line-height: 1.45; }
          .bar { height: 10px; border-radius: 99px; overflow: hidden; background: #e6eef5; margin-top: 28px; }
          .bar::before {
            content: "";
            display: block;
            width: 45%;
            height: 100%;
            border-radius: inherit;
            background: linear-gradient(90deg, #63c2b5, #2c8dcc);
            animation: load 1.2s ease-in-out infinite alternate;
          }
          @keyframes load { from { transform: translateX(-35%); } to { transform: translateX(160%); } }
        </style>
      </head>
      <body>
        <main>
          <h1>${title}</h1>
          <p>${detail}</p>
          <div class="bar"></div>
        </main>
      </body>
    </html>
  `)}`;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1180,
    minHeight: 780,
    backgroundColor: '#e7eef5',
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.setMenuBarVisibility(false);
  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.webContents.on('will-navigate', (event) => {
    const targetUrl = event.url;
    if (!targetUrl.startsWith('file://') && !targetUrl.startsWith('data:')) {
      event.preventDefault();
    }
  });

  mainWindow.loadURL(startupHtml('Zaganjam KonfMaker', 'Lokalni optimizacijski engine se pripravlja. To običajno traja nekaj sekund.'));
  return mainWindow;
}

async function boot() {
  createWindow();

  try {
    startBackend();
  } catch (error) {
    await showStartupError(error);
    return;
  }

  const backendReady = await waitForBackend();
  if (!backendReady) {
    await showStartupError(new Error('Lokalni engine se ni odzval v 60 sekundah.'));
    return;
  }

  const indexPath = path.join(ROOT_DIR, 'frontend', 'dist', 'index.html');
  await mainWindow.loadFile(indexPath);
}

async function showStartupError(error) {
  const message = error instanceof Error ? error.message : String(error);
  await dialog.showMessageBox(mainWindow, {
    type: 'error',
    title: 'KonfMaker se ni zagnal',
    message: 'Lokalni engine se ni zagnal.',
    detail: message,
  });

  if (mainWindow) {
    mainWindow.loadURL(startupHtml('KonfMaker se ni zagnal', message));
  }
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) {
        mainWindow.restore();
      }
      mainWindow.focus();
    }
  });

  app.whenReady().then(boot);
  app.on('before-quit', () => {
    app.isQuitting = true;
    stopBackend();
  });
  app.on('window-all-closed', () => {
    app.quit();
  });
}
