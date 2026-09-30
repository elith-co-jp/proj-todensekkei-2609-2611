const state = {
  activeJobId: null,
  pollTimer: null,
}

const $ = (selector) => document.querySelector(selector)

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))

function setText(selector, value) {
  const element = $(selector)
  if (element) element.textContent = value
}

function setVisible(selector, visible) {
  const element = $(selector)
  if (element) element.classList.toggle('hidden', !visible)
}

async function fetchJson(url, options) {
  const response = await fetch(url, options)
  const text = await response.text()
  let payload = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = { detail: text }
    }
  }
  if (!response.ok) {
    const message = payload?.detail || `HTTP ${response.status}`
    throw new Error(Array.isArray(message) ? message.join(', ') : message)
  }
  return payload
}

function statusClass(status) {
  return `status-chip status-${esc(status || 'queued')}`
}

function compactPath(path) {
  if (!path) return ''
  if (path.length <= 72) return path
  return `...${path.slice(-69)}`
}

function qualityPills(quality) {
  if (!quality) return ''
  const parts = [
    ['検出シンボル', quality.symbol_count],
    ['接続候補', quality.net_count],
    ['配線との接続', quality.symbol_wire_link_count],
    ['端子接続', quality.symbol_terminal_link_count],
    ['別ページ参照', quality.external_reference_count],
  ].filter(([, value]) => value !== undefined && value !== null)
  return parts.map(([key, value]) => `<span class="pill">${esc(key)}: ${esc(value)}</span>`).join('')
}

function renderJobs(jobs) {
  const container = $('#jobs-list')
  if (!container) return
  if (!jobs?.length) {
    container.innerHTML = '<div class="summary empty">履歴はまだありません。</div>'
    return
  }
  container.innerHTML = jobs
    .map((job) => {
      const pages = job.input?.pages?.join(', ') || ''
      return `
        <div class="job-row">
          <span class="${statusClass(job.status)}">${esc(job.status)}</span>
          <span class="muted">${esc(job.created_at || '')}</span>
          <div>
            <div>${esc(compactPath(job.input?.pdf || ''))}</div>
            <div class="muted">pages: ${esc(pages)}</div>
          </div>
          <button type="button" class="button secondary" data-open-job="${esc(job.id)}">開く</button>
        </div>
      `
    })
    .join('')
}

function renderResults(job) {
  const outputs = job.outputs
  const resultSummary = $('#result-summary')
  const pageResults = $('#page-results')
  const archive = $('#download-archive')
  if (!outputs?.summary) {
    resultSummary.className = 'summary empty'
    resultSummary.textContent = job.status === 'failed' ? '実行に失敗しました。ログを確認してください。' : '結果はまだありません。'
    pageResults.innerHTML = ''
    archive.classList.add('hidden')
    return
  }

  resultSummary.className = 'summary'
  resultSummary.innerHTML = [
    `<span class="pill">状態: ${esc(outputs.summary.status || job.status)}</span>`,
    `<span class="pill">対象ページ: ${esc((outputs.summary.pages || []).join(', '))}</span>`,
    outputs.summary_json_url ? `<a class="pill" href="${esc(outputs.summary_json_url)}" target="_blank" rel="noreferrer">実行概要</a>` : '',
  ].join('')

  archive.href = outputs.archive_url || '#'
  archive.classList.toggle('hidden', !outputs.archive_url)

  pageResults.innerHTML = (outputs.pages || [])
    .map((page) => {
      const links = [
        page.final_json_url ? `<a href="${esc(page.final_json_url)}" target="_blank" rel="noreferrer">ページ解析データ</a>` : '',
        page.labeled_connections_url ? `<a href="${esc(page.labeled_connections_url)}" target="_blank" rel="noreferrer">接続一覧</a>` : '',
        page.external_references_url ? `<a href="${esc(page.external_references_url)}" target="_blank" rel="noreferrer">別ページ参照</a>` : '',
      ].join('')
      return `
        <article class="page-item">
          <div class="page-title-row">
            <div>
              <h3>page_${esc(String(page.page).padStart(3, '0'))}</h3>
              <div class="summary">${qualityPills(page.quality)}</div>
            </div>
          </div>
          ${page.review_url ? `<img class="review-image" src="${esc(page.review_url)}" alt="page ${esc(page.page)} review" />` : ''}
          <details class="detail-links">
            <summary>出力ファイル</summary>
            <div class="page-links">${links}</div>
          </details>
        </article>
      `
    })
    .join('')
}

function updateStatus(job) {
  const active = ['queued', 'running', 'stopping'].includes(job.status)
  setVisible('#status', true)
  setVisible('#stop-job', active)
  setText('#status-title', job.status)
  setText('#status-detail', `job ${job.id} / return code: ${job.return_code ?? '-'}`)
  $('#submit-job').disabled = active
  $('#job-log').textContent = job.log || ''
  renderResults(job)
}

async function loadJob(jobId) {
  const payload = await fetchJson(`/api/jobs/${jobId}`)
  state.activeJobId = jobId
  updateStatus(payload.job)
  if (['queued', 'running', 'stopping'].includes(payload.job.status)) {
    startPolling()
  } else {
    stopPolling()
    await loadDefaults()
  }
}

function startPolling() {
  stopPolling()
  state.pollTimer = window.setInterval(() => {
    if (state.activeJobId) {
      loadJob(state.activeJobId).catch((error) => {
        $('#job-log').textContent = error.message
        stopPolling()
      })
    }
  }, 2500)
}

function stopPolling() {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer)
    state.pollTimer = null
  }
}

async function loadDefaults() {
  const payload = await fetchJson('/api/defaults')
  setText('#job-root', payload.job_root || '')
  $('#python-executable').value = payload.python_executable || ''
  if (!$('#model-path').value) {
    $('#model-path').value = payload.default_model || ''
  }
  renderJobs(payload.jobs || [])
}

async function submitJob(event) {
  event.preventDefault()
  const form = $('#job-form')
  const data = new FormData(form)
  if (!$('#pdf-file').files.length) data.delete('pdf_file')
  data.delete('annotation_zip_file')
  data.delete('model_file')
  data.set('run_ocr', $('#run-ocr').value || 'true')
  data.set('external_reference_source', $('#include-external-reference').checked ? 'annotation-other-label' : 'none')

  try {
    $('#submit-job').disabled = true
    const payload = await fetchJson('/api/jobs', { method: 'POST', body: data })
    state.activeJobId = payload.job.id
    updateStatus(payload.job)
    startPolling()
    await loadDefaults()
  } catch (error) {
    $('#submit-job').disabled = false
    setVisible('#status', true)
    setText('#status-title', '入力エラー')
    setText('#status-detail', error.message)
  }
}

async function stopJob() {
  if (!state.activeJobId) return
  await fetchJson(`/api/jobs/${state.activeJobId}/stop`, { method: 'POST' })
  await loadJob(state.activeJobId)
}

async function seedDemoJob() {
  try {
    const button = $('#seed-demo-job')
    button.disabled = true
    const payload = await fetchJson('/api/demo-job', { method: 'POST' })
    state.activeJobId = payload.job.id
    updateStatus(payload.job)
    await loadDefaults()
  } catch (error) {
    setVisible('#status', true)
    setText('#status-title', 'デモ作成エラー')
    setText('#status-detail', error.message)
  } finally {
    $('#seed-demo-job').disabled = false
  }
}

document.addEventListener('click', (event) => {
  const openButton = event.target.closest('[data-open-job]')
  if (openButton) {
    loadJob(openButton.dataset.openJob).catch((error) => {
      setVisible('#status', true)
      setText('#status-title', '読み込みエラー')
      setText('#status-detail', error.message)
    })
  }
})

$('#job-form').addEventListener('submit', submitJob)
$('#refresh-defaults').addEventListener('click', () => loadDefaults())
$('#seed-demo-job').addEventListener('click', seedDemoJob)
$('#stop-job').addEventListener('click', stopJob)

$('#pdf-file').addEventListener('change', () => {
  const file = $('#pdf-file').files[0]
  $('#pdf-file-name').textContent = file
    ? `${file.name} を選択中`
    : 'PDFだけ指定すれば、YOLO検出・配線検出・接続グラフ化・JSON出力まで実行します。'
})

const dropzone = $('#pdf-dropzone')
dropzone.addEventListener('dragenter', () => dropzone.classList.add('dragging'))
dropzone.addEventListener('dragover', (event) => {
  event.preventDefault()
  dropzone.classList.add('dragging')
})
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragging'))
dropzone.addEventListener('drop', () => {
  dropzone.classList.remove('dragging')
  window.setTimeout(() => $('#pdf-file').dispatchEvent(new Event('change')), 0)
})

loadDefaults().catch((error) => {
  setVisible('#status', true)
  setText('#status-title', '起動エラー')
  setText('#status-detail', error.message)
})
