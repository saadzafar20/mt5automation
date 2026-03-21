const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: 'PlatAlgo Relay',
    titleBarStyle: 'hiddenInset', // sleek title bar on Mac
    backgroundColor: '#081410',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // In dev mode, load from Vite dev server
  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    // Production: load built files
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  app.quit();
});

app.on('activate', () => {
  if (mainWindow === null) createWindow();
});

// ── IPC Handlers (native bridge) ──

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
