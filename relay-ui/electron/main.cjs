const { app, BrowserWindow, ipcMain, shell, dialog, Notification } = require('electron');
const { autoUpdater } = require('electron-updater');
const path = require('path');

let mainWindow = null;

// ── Auto-updater setup ──

autoUpdater.autoDownload = false;  // F3: user must confirm before download begins
autoUpdater.autoInstallOnAppQuit = true;

autoUpdater.on('update-available', (info) => {
  // F3: prompt user before downloading — no silent installs
  dialog
    .showMessageBox({
      type: 'info',
      title: 'Update Available',
      message: `Version ${info.version} is available. Download and install?`,
      buttons: ['Download', 'Later'],
      defaultId: 0,
    })
    .then(({ response }) => {
      if (response === 0) autoUpdater.downloadUpdate();
    });
  if (mainWindow) {
    mainWindow.webContents.send('update-status', {
      status: 'available',
      version: info.version,
    });
  }
});

autoUpdater.on('download-progress', (progress) => {
  if (mainWindow) {
    mainWindow.webContents.send('update-status', {
      status: 'downloading',
      percent: Math.round(progress.percent),
    });
  }
});

autoUpdater.on('update-downloaded', (info) => {
  if (mainWindow) {
    mainWindow.webContents.send('update-status', {
      status: 'ready',
      version: info.version,
    });
  }
  // Prompt user to restart
  dialog
    .showMessageBox(mainWindow, {
      type: 'info',
      title: 'Update Ready',
      message: `Version ${info.version} has been downloaded. Restart now to update?`,
      buttons: ['Restart', 'Later'],
      defaultId: 0,
    })
    .then(({ response }) => {
      if (response === 0) {
        // setImmediate gives the dialog time to close before quit
        setImmediate(() => autoUpdater.quitAndInstall(false, true));
      }
    });
});

autoUpdater.on('update-not-available', (info) => {
  console.log('Auto-update: already on latest version', info?.version);
});

autoUpdater.on('checking-for-update', () => {
  console.log('Auto-update: checking for updates at', autoUpdater.getFeedURL());
});

autoUpdater.on('error', (err) => {
  console.error('Auto-update error:', err?.message || err);
  if (mainWindow) {
    mainWindow.webContents.send('update-status', { status: 'error', error: err?.message });
  }
});

// ── Window ──

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: 'PlatAlgo Relay',
    titleBarStyle: 'hidden',
    trafficLightPosition: { x: 16, y: 18 },
    backgroundColor: '#081410',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  createWindow();

  // F4: Content Security Policy — restrict what the renderer can load
  const { session } = require('electron');
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          "default-src 'self'; " +
          "script-src 'self'; " +
          "style-src 'self' 'unsafe-inline'; " +
          "img-src 'self' data: https:; " +
          "connect-src 'self' https://app.platalgo.com https://api.telegram.org wss: ws:; " +
          "font-src 'self' data:; " +
          "object-src 'none';"
        ],
      },
    });
  });

  // Check for updates after launch (not in dev mode)
  if (!process.env.VITE_DEV_SERVER_URL) {
    setTimeout(() => autoUpdater.checkForUpdates(), 3000);
  }
});

app.on('window-all-closed', () => {
  app.quit();
});

app.on('activate', () => {
  if (mainWindow === null) createWindow();
});

// ── IPC Handlers ──

ipcMain.handle('open-external', async (_event, url) => {
  if (url && url.startsWith('https://')) {
    await shell.openExternal(url);
    return true;
  }
  return false;
});

ipcMain.handle('get-platform', () => process.platform);

ipcMain.handle('keyring-get', async (_event, service, userId) => {
  try {
    const keytar = require('keytar');
    return (await keytar.getPassword(service, userId)) || '';
  } catch {
    return '';
  }
});

ipcMain.handle('keyring-set', async (_event, service, userId, password) => {
  try {
    const keytar = require('keytar');
    await keytar.setPassword(service, userId, password);
    return true;
  } catch {
    return false;
  }
});

ipcMain.handle('clipboard-write', async (_event, text) => {
  const { clipboard } = require('electron');
  clipboard.writeText(text);
  return true;
});

ipcMain.handle('get-version', () => app.getVersion());

ipcMain.handle('is-startup-enabled', async () => {
  return app.getLoginItemSettings().openAtLogin;
});

ipcMain.handle('set-startup', async (_event, enabled) => {
  app.setLoginItemSettings({ openAtLogin: enabled });
  return true;
});

ipcMain.handle('last-user-get', async () => {
  const fs = require('fs');
  const filePath = path.join(app.getPath('userData'), 'last_user.json');
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
  } catch {
    return {};
  }
});

ipcMain.handle('last-user-set', async (_event, data) => {
  const fs = require('fs');
  const filePath = path.join(app.getPath('userData'), 'last_user.json');
  fs.writeFileSync(filePath, JSON.stringify(data), 'utf-8');
  return true;
});

ipcMain.handle('check-for-updates', () => {
  autoUpdater.checkForUpdates();
});

ipcMain.handle('install-update', () => {
  // F-03: Require explicit user confirmation before restarting, even when called
  // programmatically from the renderer, to prevent forced-restart abuse.
  dialog
    .showMessageBox(mainWindow, {
      type: 'info',
      title: 'Restart to Update',
      message: 'Restart PlatAlgo Relay to apply the downloaded update?',
      buttons: ['Restart Now', 'Later'],
      defaultId: 0,
    })
    .then(({ response }) => {
      if (response === 0) {
        setImmediate(() => autoUpdater.quitAndInstall(false, true));
      }
    });
});

ipcMain.handle('show-notification', (_event, { title, body }) => {
  if (Notification.isSupported()) {
    new Notification({ title: String(title), body: String(body) }).show()
  }
});
