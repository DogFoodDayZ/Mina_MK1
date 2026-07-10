#!/usr/bin/env node
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const publicDir = path.join(__dirname, 'public');
const PORT = process.env.AVATAR_PORT || 4311;

let avatarState = {
  state: 'idle',
  mouth: 'closed',
  emotion: 'neutral',
  updatedAt: new Date().toISOString(),
};

const sseClients = new Set();

function broadcastState() {
  const payload = JSON.stringify(avatarState);
  for (const res of sseClients) {
    res.write(`data: ${payload}\n\n`);
  }
}

function sendJson(res, status, data) {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(body),
    'Access-Control-Allow-Origin': '*',
  });
  res.end(body);
}

function serveStatic(req, res) {
  const parsed = url.parse(req.url);
  let pathname = parsed.pathname || '/';
  if (pathname === '/') pathname = '/index.html';
  const filePath = path.join(publicDir, pathname);
  if (!filePath.startsWith(publicDir)) {
    sendJson(res, 403, { error: 'Forbidden' });
    return;
  }
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404);
      res.end('Not found');
      return;
    }
    const ext = path.extname(filePath).toLowerCase();
    const types = {
      '.html': 'text/html',
      '.css': 'text/css',
      '.js': 'application/javascript',
      '.png': 'image/png',
      '.svg': 'image/svg+xml',
    };
    res.writeHead(200, {
      'Content-Type': types[ext] || 'application/octet-stream',
      'Access-Control-Allow-Origin': '*',
    });
    res.end(data);
  });
}

const server = http.createServer((req, res) => {
  const parsed = url.parse(req.url, true);
  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    });
    res.end();
    return;
  }

  if (parsed.pathname === '/state') {
    if (req.method === 'GET') {
      sendJson(res, 200, avatarState);
      return;
    }
    if (req.method === 'POST') {
      let body = '';
      req.on('data', chunk => (body += chunk));
      req.on('end', () => {
        try {
          const incoming = JSON.parse(body || '{}');
          const allowed = ['state', 'mouth', 'emotion'];
          let changed = false;
          for (const key of allowed) {
            if (incoming[key]) {
              avatarState[key] = incoming[key];
              changed = true;
            }
          }
          avatarState.updatedAt = new Date().toISOString();
          if (changed) {
            broadcastState();
          }
          sendJson(res, 200, avatarState);
        } catch (err) {
          sendJson(res, 400, { error: 'Invalid JSON payload' });
        }
      });
      return;
    }
  }

  if (parsed.pathname === '/events' && req.method === 'GET') {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
      'Access-Control-Allow-Origin': '*',
    });
    res.write(`data: ${JSON.stringify(avatarState)}\n\n`);
    sseClients.add(res);
    req.on('close', () => {
      sseClients.delete(res);
    });
    return;
  }

  serveStatic(req, res);
});

server.listen(PORT, () => {
  console.log(`Avatar overlay server running on http://localhost:${PORT}`);
});
