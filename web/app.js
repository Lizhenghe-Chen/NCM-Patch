const el = {
  sourceRow: document.getElementById('sourceRow'),
  sourcePath: document.getElementById('sourcePath'),
  outputRow: document.getElementById('outputRow'),
  outputPath: document.getElementById('outputPath'),
  outputSame: document.getElementById('outputSame'),
  jobs: document.getElementById('jobs'),
  remove: document.getElementById('remove'),
  scan: document.getElementById('scan'),
  start: document.getElementById('start'),
  cancel: document.getElementById('cancel'),
  msg: document.getElementById('msg'),
  tag: document.getElementById('stateTag'),
  mPending: document.getElementById('mPending'),
  mConverted: document.getElementById('mConverted'),
  mFailed: document.getElementById('mFailed'),
  mSkipped: document.getElementById('mSkipped'),
  bar: document.getElementById('bar'),
  barFill: document.getElementById('barFill'),
  barText: document.getElementById('barText'),
  elapsed: document.getElementById('elapsed'),
  log: document.getElementById('log'),
  engine: document.getElementById('engine'),
  browser: document.getElementById('browser'),
  browserTitle: document.getElementById('browserTitle'),
  browserCrumbs: document.getElementById('browserCrumbs'),
  browserList: document.getElementById('browserList'),
  browserMsg: document.getElementById('browserMsg'),
  browserPath: document.getElementById('browserPath'),
  browserChoose: document.getElementById('browserChoose'),
  browserCancel: document.getElementById('browserCancel'),
  browserClose: document.getElementById('browserClose'),
};

const STATE_LABEL = { idle: '空闲', running: '运行中', done: '已完成', cancelled: '已停止' };

const state = { source: '', output: '', pickTarget: 'source', browsePath: '' };

let logSince = 0;
let pollTimer = null;
let scanInfo = null;

function setMsg(text, kind = '') {
  el.msg.className = kind ? `msg ${kind}` : 'msg';
  el.msg.textContent = text;
}

async function api(path, payload) {
  const options =
    payload === undefined
      ? {}
      : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
}

/* ---------- 路径显示 ---------- */

function renderPaths() {
  const rows = [
    [el.sourcePath, state.source, '未选择'],
    [el.outputPath, state.output, '与源目录相同'],
  ];
  for (const [node, value, placeholder] of rows) {
    node.textContent = value || placeholder;
    node.title = value || '';
    node.classList.toggle('unset', !value);
  }
  el.outputSame.hidden = !state.output;
}

function setPath(target, path) {
  state[target] = path || '';
  scanInfo = null;
  renderPaths();
  updateButtons(el.tag.dataset.state === 'running');
}

function updateButtons(running) {
  const busy = running === undefined ? el.tag.dataset.state === 'running' : running;
  const noTarget = !state.source || (scanInfo !== null && scanInfo.pending === 0);
  el.scan.disabled = busy;
  el.start.disabled = busy || noTarget;
  el.cancel.disabled = !busy;
  el.sourceRow.disabled = busy;
  el.outputRow.disabled = busy;
  el.outputSame.disabled = busy;
}

/* ---------- 选目录 ---------- */

async function pick(target) {
  const start = target === 'output' ? state.output || state.source : state.source;
  setMsg('等待系统目录对话框…若没看到，它可能被其他窗口挡住了');
  try {
    const data = await api('/api/pick-dir', { start });
    if (data.fallback) {
      openBrowser(target, start);
      return;
    }
    if (data.cancelled) {
      setMsg('');
      return;
    }
    setPath(target, data.path);
    setMsg(`已选择：${data.path}`, 'ok');
  } catch (error) {
    setMsg(error.message, 'err');
  }
}

/* ---------- 目录浏览弹层 ---------- */

function openBrowser(target, startPath) {
  state.pickTarget = target;
  el.browserTitle.textContent = target === 'output' ? '选择输出目录' : '选择源目录';
  el.browser.hidden = false;
  document.body.classList.add('modal-open');
  el.browserChoose.disabled = true;
  setMsg('');
  navigate(startPath || '');
}

function closeBrowser() {
  el.browser.hidden = true;
  document.body.classList.remove('modal-open');
}

function buildCrumbs(path) {
  const crumbs = [];
  if (/^[A-Za-z]:/.test(path)) {
    const drive = `${path.slice(0, 2)}\\`;
    crumbs.push({ label: drive, path: drive });
    let acc = drive;
    for (const part of path.slice(2).split(/[\\/]/).filter(Boolean)) {
      acc = acc.endsWith('\\') ? acc + part : `${acc}\\${part}`;
      crumbs.push({ label: part, path: acc });
    }
  } else {
    let acc = '';
    for (const part of path.split('/').filter(Boolean)) {
      acc += `/${part}`;
      crumbs.push({ label: part, path: acc });
    }
  }
  return crumbs;
}

async function navigate(path) {
  el.browserMsg.textContent = '读取中…';
  try {
    const data = await api(`/api/browse?path=${encodeURIComponent(path || '')}`);
    state.browsePath = data.path || '';
    renderCrumbs(data);
    renderEntries(data);
    el.browserPath.textContent = data.path || '此电脑';
    el.browserChoose.disabled = !data.path;
    el.browserMsg.textContent = data.error || '';
  } catch (error) {
    el.browserMsg.textContent = error.message;
  }
}

function renderCrumbs(data) {
  el.browserCrumbs.replaceChildren();
  const items = [{ label: '此电脑', path: '' }];
  if (data.path) items.push(...buildCrumbs(data.path));

  items.forEach((item, index) => {
    const last = index === items.length - 1;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = last ? 'crumb last' : 'crumb';
    button.textContent = item.label;
    button.title = item.path || '此电脑';
    if (!last) button.addEventListener('click', () => navigate(item.path));
    el.browserCrumbs.append(button);
    if (!last) {
      const separator = document.createElement('span');
      separator.textContent = '›';
      el.browserCrumbs.append(separator);
    }
  });
}

function renderEntries(data) {
  el.browserList.replaceChildren();
  if (!data.path) {
    for (const drive of data.drives || []) addEntry(drive, drive, '›');
    return;
  }
  if (data.parent) addEntry('上一层', data.parent, '↑', 'up');
  for (const dir of data.dirs || []) addEntry(dir.name, dir.path, '›');
}

function addEntry(label, path, chevron, extra = '') {
  const item = document.createElement('li');
  const button = document.createElement('button');
  button.type = 'button';
  button.className = extra ? `dirow ${extra}` : 'dirow';

  const name = document.createElement('span');
  name.textContent = label;
  const chev = document.createElement('span');
  chev.className = 'chev';
  chev.textContent = chevron;

  button.append(name, chev);
  button.addEventListener('click', () => navigate(path));
  item.append(button);
  el.browserList.append(item);
}

function chooseCurrent() {
  if (!state.browsePath) return;
  setPath(state.pickTarget, state.browsePath);
  setMsg(`已选择：${state.browsePath}`, 'ok');
  closeBrowser();
}

/* ---------- 拖拽 ---------- */

function readFirstNames(entry, limit) {
  return new Promise((resolve) => {
    let reader;
    try {
      reader = entry.createReader();
    } catch {
      resolve([]);
      return;
    }
    const collected = [];
    const readBatch = () => {
      reader.readEntries(
        (batch) => {
          if (!batch.length) {
            resolve(collected.slice(0, limit));
            return;
          }
          for (const item of batch) {
            if (collected.length < limit) collected.push(item.name);
          }
          readBatch();
        },
        () => resolve(collected.slice(0, limit))
      );
    };
    readBatch();
  });
}

function bindDrop(row, target) {
  row.addEventListener('dragover', (event) => {
    event.preventDefault();
    row.classList.add('dropping');
  });
  row.addEventListener('dragleave', () => row.classList.remove('dropping'));
  row.addEventListener('drop', (event) => {
    row.classList.remove('dropping');
    handleDrop(event, target);
  });
}

async function handleDrop(event, target) {
  event.preventDefault();
  const item = event.dataTransfer && event.dataTransfer.items ? event.dataTransfer.items[0] : null;
  const entry = item && typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null;
  if (!entry) {
    setMsg('没读懂拖入的内容，请点「选择…」', 'warn');
    return;
  }

  setMsg(`正在定位「${entry.name}」…`);
  const payload = {
    name: entry.name,
    items: entry.isDirectory ? await readFirstNames(entry, 40) : [],
    roots: [state.source, state.output].filter(Boolean),
  };

  try {
    const data = await api('/api/resolve-drop', payload);
    if (data.path) {
      setPath(target, data.path);
      setMsg(`已识别：${data.path}`, 'ok');
    } else if (data.candidates) {
      showCandidates(data.candidates, target);
    } else {
      setMsg(data.error || '没能定位这个文件夹，请点「选择…」', 'warn');
    }
  } catch (error) {
    setMsg(error.message, 'err');
  }
}

function showCandidates(paths, target) {
  el.msg.className = 'msg warn';
  el.msg.textContent = '有多个同名目录，点一个：';
  for (const path of paths) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'chip';
    chip.textContent = path;
    chip.title = path;
    chip.addEventListener('click', () => {
      setPath(target, path);
      setMsg(`已选择：${path}`, 'ok');
    });
    el.msg.append(chip);
  }
}

/* ---------- 进度与日志 ---------- */

function classify(text) {
  if (text.includes('✅')) return 'ok';
  if (text.includes('❌')) return 'err';
  if (text.includes('⚠️') || text.includes('⏹️')) return 'warn';
  if (text.includes('⏭️')) return 'skip';
  return '';
}

function appendLog(lines, className = '') {
  const empty = el.log.querySelector('.empty');
  if (empty) empty.remove();

  const atBottom = el.log.scrollHeight - el.log.scrollTop - el.log.clientHeight < 24;
  for (const text of lines) {
    const line = document.createElement('p');
    line.textContent = text;
    const kind = className || classify(text);
    if (kind) line.className = kind;
    el.log.appendChild(line);
  }
  while (el.log.childElementCount > 1000) el.log.removeChild(el.log.firstElementChild);
  if (atBottom) el.log.scrollTop = el.log.scrollHeight;
}

function clearLog() {
  el.log.replaceChildren();
}

function updateBar(done, total) {
  const percent = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  el.barFill.style.width = `${percent}%`;
  el.bar.setAttribute('aria-valuenow', String(percent));
  el.barText.textContent = total ? `${done} / ${total} 个` : '尚未开始';
}

function applyStatus(data) {
  if (Array.isArray(data.log) && data.log.length) appendLog(data.log);
  if (typeof data.log_total === 'number') logSince = data.log_total;
  if (!data.state || data.state === 'idle') {
    updateButtons(false);
    return;
  }

  const running = data.state === 'running';
  el.tag.textContent = STATE_LABEL[data.state] || data.state;
  el.tag.dataset.state = data.state;
  el.mPending.textContent = data.total ?? '—';
  el.mConverted.textContent = data.converted ?? 0;
  el.mFailed.textContent = data.failed ?? 0;
  el.mSkipped.textContent = data.skipped ?? 0;
  updateBar(data.done ?? 0, data.total ?? 0);
  el.elapsed.textContent = data.elapsed ? `用时 ${data.elapsed.toFixed(1)}s` : '';

  if (!running) scanInfo = null;
  updateButtons(running);

  if (data.state === 'done') {
    setMsg(
      `完成：成功 ${data.converted}，失败 ${data.failed}，跳过 ${data.skipped}`,
      data.failed ? 'warn' : 'ok'
    );
  } else if (data.state === 'cancelled') {
    setMsg(`已停止：成功 ${data.converted}，失败 ${data.failed}`, 'warn');
  }
}

async function poll() {
  clearTimeout(pollTimer);
  try {
    const data = await api(`/api/status?since=${logSince}`);
    applyStatus(data);
    if (data.state === 'running') pollTimer = setTimeout(poll, 700);
  } catch (error) {
    setMsg(error.message, 'err');
  }
}

/* ---------- 动作 ---------- */

async function scan() {
  if (!state.source) {
    setMsg('请先选择源目录', 'warn');
    return;
  }

  el.scan.disabled = true;
  setMsg('扫描中…');
  try {
    const data = await api('/api/scan', { source: state.source, output: state.output });
    scanInfo = { pending: data.pending, skipped: data.skipped };

    el.mPending.textContent = data.pending;
    el.mConverted.textContent = 0;
    el.mFailed.textContent = 0;
    el.mSkipped.textContent = data.skipped;
    el.tag.textContent = STATE_LABEL.idle;
    el.tag.dataset.state = 'idle';
    el.elapsed.textContent = '';
    updateBar(0, data.pending);

    clearLog();
    appendLog(
      [`共 ${data.total} 个 .ncm：待转换 ${data.pending}，已跳过 ${data.skipped}`, `输出目录 ${data.dest}`],
      'head'
    );
    if (data.preview.length) {
      appendLog(
        data.preview.map((name, index) => ` ${String(index + 1).padStart(3, ' ')}  ${name}`),
        'item'
      );
      if (data.pending > data.preview.length) {
        appendLog([` … 还有 ${data.pending - data.preview.length} 个未列出`], 'skip');
      }
    } else {
      appendLog(['没有待转换的文件：全部都有对应的 .flac / .mp3。'], 'skip');
    }

    setMsg(
      data.pending ? `待转换 ${data.pending} 个，已跳过 ${data.skipped} 个` : '没有待转换的文件',
      data.pending ? 'ok' : 'warn'
    );
  } catch (error) {
    setMsg(error.message, 'err');
    scanInfo = null;
  } finally {
    updateButtons(el.tag.dataset.state === 'running');
  }
}

async function start() {
  if (!state.source) {
    setMsg('请先选择源目录', 'warn');
    return;
  }

  el.start.disabled = true;
  el.scan.disabled = true;
  setMsg('正在启动…');
  try {
    const data = await api('/api/start', {
      source: state.source,
      output: state.output,
      jobs: Number(el.jobs.value) || 4,
      remove_source: el.remove.checked,
    });
    logSince = 0;
    clearLog();
    el.elapsed.textContent = '';
    el.tag.textContent = STATE_LABEL.running;
    el.tag.dataset.state = 'running';
    updateBar(0, data.total);
    setMsg(`开始转换 ${data.total} 个，跳过 ${data.skipped} 个`, 'ok');
    updateButtons(true);
    poll();
  } catch (error) {
    setMsg(error.message, 'err');
    updateButtons(false);
  }
}

async function cancel() {
  el.cancel.disabled = true;
  try {
    await api('/api/cancel', {});
    setMsg('已请求停止，正在进行的文件会跑完', 'warn');
  } catch (error) {
    setMsg(error.message, 'err');
  }
}

/* ---------- 初始化 ---------- */

async function init() {
  el.sourceRow.addEventListener('click', () => pick('source'));
  el.outputRow.addEventListener('click', () => pick('output'));
  el.outputSame.addEventListener('click', () => {
    setPath('output', '');
    setMsg('输出目录已设为与源目录相同', 'ok');
  });
  el.scan.addEventListener('click', scan);
  el.start.addEventListener('click', start);
  el.cancel.addEventListener('click', cancel);

  el.browserChoose.addEventListener('click', chooseCurrent);
  el.browserCancel.addEventListener('click', closeBrowser);
  el.browserClose.addEventListener('click', closeBrowser);
  el.browser.addEventListener('click', (event) => {
    if (event.target === el.browser) closeBrowser();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !el.browser.hidden) closeBrowser();
  });

  bindDrop(el.sourceRow, 'source');
  bindDrop(el.outputRow, 'output');
  for (const type of ['dragover', 'drop']) {
    window.addEventListener(type, (event) => {
      if (!el.browser.hidden) return;
      event.preventDefault();
    });
  }

  renderPaths();
  updateButtons(false);

  try {
    const config = await api('/api/settings');
    setPath('source', config.settings.source);
    setPath('output', config.settings.output);
    el.jobs.value = config.settings.jobs;
    el.remove.checked = Boolean(config.settings.remove_source);
    el.engine.textContent = config.engine
      ? `${config.engine.name}（${config.engine.requirements}）`
      : '引擎信息不可用';
  } catch (error) {
    el.engine.textContent = '无法连接后端';
    setMsg(error.message, 'err');
  }

  try {
    const status = await api('/api/status');
    if (status.state && status.state !== 'idle') {
      logSince = 0;
      applyStatus(status);
      if (status.state === 'running') poll();
    }
  } catch {
    /* 状态接口不可用时保持空闲界面 */
  }
}

init();
