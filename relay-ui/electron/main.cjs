const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron');
const { autoUpdater } = require('electron-updater');
const path = require('path');

let mainWindow = null;

// ── Auto-updater setup ──

autoUpdater.autoDownload = true;
autoUpdater.autoInstallOnAppQuit = true;

autoUpdater.on('update-available', (info) => {
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
        autoUpdater.quitAndInstall();
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
  if (url && url.startsWith('http')) {
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
  autoUpdater.quitAndInstall();
});
