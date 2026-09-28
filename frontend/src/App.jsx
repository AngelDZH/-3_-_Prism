import { useState } from 'react'
import { MapContainer, TileLayer, CircleMarker, Popup, Polyline } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import './App.css'

const COLORS = ['#e6194b','#3cb44b','#c9a400','#4363d8','#f58231','#911eb4',
                '#0aa3a3','#d6489a','#7a8f00','#e08fa0','#008080','#8a6fe0']

const VARIANT_LABELS = {
  A: 'Вариант A — быстрое построение',
  B: 'Вариант B — экономия пробега',
  C: 'Вариант C — альтернативная эвристика',
}

function App() {
  const [plan, setPlan] = useState(null)
  const [baseline, setBaseline] = useState(null)
  const [variants, setVariants] = useState(null)
  const [replanResult, setReplanResult] = useState(null)
  const [originalPlan, setOriginalPlan] = useState(null)
  const [loading, setLoading] = useState(false)
  const [variantsLoading, setVariantsLoading] = useState(false)
  const [replanLoading, setReplanLoading] = useState(false)
  const [view, setView] = useState('data')

  const ensureBaseline = async () => {
    if (baseline) return baseline
    const resp = await fetch('http://localhost:8000/baseline', { method: 'POST' })
    const data = await resp.json()
    setBaseline(data)
    return data
  }

  const loadPlan = async () => {
    setLoading(true)
    const [planResp] = await Promise.all([
      fetch('http://localhost:8000/plan', { method: 'POST' }),
      ensureBaseline(),
    ])
    const data = await planResp.json()
    setPlan(data)
    setOriginalPlan(data)
    setLoading(false)
    setView('dayplan')
  }


  const loadVariants = async () => {
    setVariantsLoading(true)
    const [variantsResp] = await Promise.all([
      fetch('http://localhost:8000/plan-variants', { method: 'POST' }),
      ensureBaseline(),
    ])
    const data = await variantsResp.json()
    setVariants(data.variants)
    setVariantsLoading(false)
  }

  const chooseVariant = (variant) => {
    setPlan(variant)
    setOriginalPlan(variant)
    setView('dayplan')
  }

  const resetPlan = () => {
    if (originalPlan) {
      setPlan(originalPlan)
      setReplanResult(null)
    }
  }

  const doReassign = async (requestId, engineerId) => {
    if (!engineerId) return
    const params = new URLSearchParams({ request_id: requestId, target_engineer_id: engineerId })
    const resp = await fetch(`http://localhost:8000/reassign?${params}`, { method: 'POST' })
    const result = await resp.json()
    if (result.error) {
      alert(result.error)
    } else {
      setPlan(result)
    }
  }

  const loadReplan = async (extraParams) => {
    setReplanLoading(true)
    const params = new URLSearchParams(extraParams)
    const resp = await fetch(`http://localhost:8000/replan?${params}`, { method: 'POST' })
    const data = await resp.json()
    setReplanResult(data)
    if (data.new_plan) {
      setPlan(data.new_plan)
    }
    setReplanLoading(false)
  }

  const engineerColor = (id) => {
    const num = parseInt(id.replace('ENG_', ''), 10)
    return COLORS[(num - 1) % COLORS.length]
  }

  const kmByEngineer = (eid) => plan?.engineers_summary?.find(e => e.engineer_id === eid)?.km ?? 0

  const routesByEngineer = plan ? Object.entries(
    plan.assigned.reduce((acc, a) => {
      (acc[a.engineer_id] ??= []).push(a)
      return acc
    }, {})
  ).map(([eng, jobs]) => {
    const sorted = [...jobs].sort((a, b) => a.arrival.localeCompare(b.arrival))
    const coords = [[plan.depot.lat, plan.depot.lon], ...sorted.map(j => [j.lat, j.lon])]
    return { eng, coords }
  }) : []

  const NavButton = ({ id, label }) => (
    <button className={`nav-tab ${view === id ? 'active' : ''}`} onClick={() => setView(id)}>
      {label}
    </button>
  )

  return (
    <div className="app-shell">
      <div className="sidebar">
        <p className="brand">Билайн — маршруты</p>
        <p className="brand-sub">Восток · планирование выездных бригад</p>

        <div className="nav-tabs">
          <NavButton id="data" label="Данные" />
          <NavButton id="dayplan" label="План дня" />
          <NavButton id="problems" label="Проблемы" />
          <NavButton id="replan" label="Перепланирование" />
        </div>

        {view === 'data' && (
          <div>
            <p className="hint-text">Восток: 68 заявок, 12 бригад</p>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
              <button className="btn btn-primary" onClick={loadPlan} disabled={loading}>
                {loading ? 'Считаю (до 20 сек)...' : 'Построить план'}
              </button>
              <button className="btn btn-secondary" onClick={loadVariants} disabled={variantsLoading}>
                {variantsLoading ? 'Считаю 3 варианта...' : 'Показать 3 варианта плана'}
              </button>
            </div>

            {variants && (
              <div style={{ marginTop: 16 }}>
                <p style={{ fontWeight: 700, fontSize: 13 }}>Выберите вариант плана:</p>
                {variants.map(v => (
                  <div key={v.variant} className="card variant-card">
                    <div className="variant-title">{VARIANT_LABELS[v.variant] || `Вариант ${v.variant}`}</div>
                    <table className="metrics-table" style={{ marginTop: 8 }}>
                      <tbody>
                        <tr><td>Выполнено</td><td className="num">{v.metrics.assigned}/{v.metrics.total}</td></tr>
                        <tr><td>Бригад</td><td className="num">{v.metrics.engineers_used}</td></tr>
                        <tr><td>Пробег</td><td className="num">{v.metrics.total_km} км</td></tr>
                        <tr><td>⚠ Риск опоздания</td><td className="num">{v.assigned.filter(a => a.risk).length}</td></tr>
                      </tbody>
                    </table>
                    <button className="btn btn-primary btn-small" style={{ marginTop: 10 }} onClick={() => chooseVariant(v)}>
                      Выбрать этот вариант
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {view === 'dayplan' && plan && baseline && (
                  <div>
                    <div className="card">
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                        <span style={{ fontWeight: 700, fontSize: 13 }}>Сравнение с бейзлайном</span>
                        <button className="btn btn-secondary btn-small" onClick={resetPlan} disabled={!originalPlan || plan === originalPlan}>
                          ↺ Вернуть исходный план
                        </button>
                      </div>
                      <table className="metrics-table">

                <thead>
                  <tr><th></th><th>Солвер</th><th>Бейзлайн</th></tr>
                </thead>
                <tbody>
                  <tr><td>Выполнено</td><td className="num">{plan.metrics.assigned}/{plan.metrics.total}</td><td className="num">{baseline.metrics.assigned}/{baseline.metrics.total}</td></tr>
                  <tr><td>Бригад</td><td className="num">{plan.metrics.engineers_used}</td><td className="num">{baseline.metrics.engineers_used}</td></tr>
                  <tr><td>Пробег</td><td className="num">{plan.metrics.total_km} км</td><td className="num">{baseline.metrics.total_km} км</td></tr>
                  <tr><td>⚠ Риск опоздания</td><td className="num">{plan.assigned.filter(a => a.risk).length}</td><td>—</td></tr>
                </tbody>
              </table>
            </div>

            {Object.entries(
              plan.assigned.reduce((acc, a) => {
                (acc[a.engineer_id] ??= []).push(a)
                return acc
              }, {})
            ).map(([eng, jobs]) => (
              <div key={eng} className="engineer-card" style={{ '--eng-color': engineerColor(eng) }}>
                <div className="engineer-header">
                  <span className="engineer-dot" style={{ '--eng-color': engineerColor(eng) }} />
                  {eng} — {kmByEngineer(eng)} км
                </div>
                {jobs.map(j => (
                  <div key={j.request_id} className={`job-row ${j.risk ? 'risk' : ''}`}>
                    <span>{j.risk && '⚠ '}{j.arrival} — {j.request_id}</span>
                    <select
                      className="select-small"
                      value=""
                      onChange={(ev) => doReassign(j.request_id, ev.target.value)}
                    >
                      <option value="">переназначить...</option>
                      {plan.engineers_summary
                        .filter(e => e.engineer_id !== eng)
                        .map(e => {
                          const eligible = e.skills.includes(j.required_skill) &&
                            (!j.required_transport || e.transport_type === j.required_transport)
                          return (
                            <option key={e.engineer_id} value={e.engineer_id} disabled={!eligible}>
                              {e.engineer_id}{eligible ? '' : ' — нет допуска'}
                            </option>
                          )
                        })}
                    </select>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}

        {view === 'problems' && plan && (
          <div>
            <p style={{ fontWeight: 700, fontSize: 13 }}>Не назначено: {plan.unassigned.length}</p>
            {plan.unassigned.map(u => (
              <div key={u.request_id} className="problem-item">
                <b>{u.request_id}</b><br /><small>{u.reason}</small>
              </div>
            ))}
          </div>
        )}

        {view === 'replan' && plan && (
          <div>
            <p style={{ fontWeight: 700, fontSize: 13 }}>Что случилось?</p>

            <div className="event-card">
              <div className="event-title">🕒 Время события</div>
              <div className="event-field">
                <input id="event-cutoff" type="time" defaultValue="14:00" />
              </div>
              <p className="hint-text" style={{ fontSize: 11, margin: 0 }}>
                Используется для всех трёх типов события ниже — момент, когда это произошло.
              </p>
            </div>

            <div className="event-card">
              <div className="event-title">🆕 Новая срочная заявка</div>
              <div className="event-field">
                Навык:
                <select id="new-req-skill" defaultValue="emergency">
                  <option value="emergency">Авария</option>
                  <option value="connection">Подключение</option>
                  <option value="local">Локальная</option>
                </select>
              </div>
              <div className="event-field">
                Рядом с:
                <select id="new-req-base" defaultValue={plan.assigned[0]?.request_id}>
                  {plan.assigned.map(a => <option key={a.request_id} value={a.request_id}>{a.request_id}</option>)}
                </select>
              </div>
              <button
                className="btn btn-primary btn-small"
                disabled={replanLoading}
                onClick={() => {
                  const cutoff = document.getElementById('event-cutoff').value
                  const skill = document.getElementById('new-req-skill').value
                  const [h, m] = cutoff.split(':').map(Number)
                  const endH = Math.min(h + 2, 22)
                  loadReplan({
                    event_type: 'new_request', event_skill: skill,
                    cutoff_str: cutoff,
                    event_window_start: cutoff,
                    event_window_end: `${String(endH).padStart(2, '0')}:${String(m).padStart(2, '0')}`,
                    event_base_request: document.getElementById('new-req-base').value,
                  })
                }}
              >
                Применить
              </button>
            </div>

            <div className="event-card">
              <div className="event-title">❌ Клиент отменил заявку</div>
              <div className="event-field">
                <select id="cancel-req" defaultValue={plan.assigned[0]?.request_id}>
                  {plan.assigned.map(a => (
                    <option key={a.request_id} value={a.request_id}>
                      {a.request_id} ({a.engineer_id}, {a.arrival})
                    </option>
                  ))}
                </select>
              </div>
              <button
                className="btn btn-primary btn-small"
                disabled={replanLoading}
                onClick={() => loadReplan({
                  event_type: 'cancel',
                  cutoff_str: document.getElementById('event-cutoff').value,
                  cancel_request_id: document.getElementById('cancel-req').value,
                })}
              >
                Применить
              </button>
            </div>

            <div className="event-card">
              <div className="event-title">⚠️ Инженер стал недоступен</div>
              <div className="event-field">
                <select id="unavail-eng" defaultValue={plan.engineers_summary[0]?.engineer_id}>
                  {plan.engineers_summary.map(e => (
                    <option key={e.engineer_id} value={e.engineer_id}>{e.engineer_id} ({e.jobs} заявок)</option>
                  ))}
                </select>
              </div>
              <button
                className="btn btn-primary btn-small"
                disabled={replanLoading}
                onClick={() => loadReplan({
                  event_type: 'unavailable',
                  cutoff_str: document.getElementById('event-cutoff').value,
                  unavailable_engineer_id: document.getElementById('unavail-eng').value,
                })}
              >
                Применить
              </button>
            </div>

            {replanLoading && <p className="hint-text">Пересчитываю (до 25 сек)...</p>}

            {replanResult && !replanResult.error && (
              <div className="card" style={{ marginTop: 16 }}>
                <p>Отсечка: <b>{replanResult.cutoff}</b></p>
                <p>Уже выполнено до события (не тронуто): <b>{replanResult.done_before_cutoff}</b></p>
                <p>Без изменений после события: <b>{replanResult.unchanged_count}</b></p>
                <p>Изменилось: <b>{replanResult.affected_count}</b></p>
                <hr style={{ border: 'none', borderTop: '1px solid var(--border)' }} />
                <b style={{ fontSize: 13 }}>Что изменилось:</b>
                <ul className="diff-list" style={{ listStyle: 'none', paddingLeft: 0, marginTop: 8 }}>
                  {replanResult.changes.map((c, i) => <li key={i}>{c}</li>)}
                </ul>
                <p className="hint-text" style={{ marginTop: 8 }}>
                  Вкладка «План дня» уже обновлена новым планом — переключитесь, чтобы увидеть его на карте.
                </p>
              </div>
            )}
          </div>
        )}

        {view === 'replan' && !plan && <p className="hint-text">Сначала построй план на вкладке «Данные»</p>}
        {!plan && (view === 'dayplan' || view === 'problems') && <p className="hint-text">Сначала построй план на вкладке «Данные»</p>}
      </div>

      <div className="map-wrap">
        <MapContainer center={[55.72, 37.75]} zoom={12} style={{ height: '100%', width: '100%' }} attributionControl={false}>
          <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />

          {plan && (
            <>
              <CircleMarker center={[plan.depot.lat, plan.depot.lon]} radius={10} pathOptions={{ color: 'black', fillColor: 'black', fillOpacity: 1 }}>
                <Popup>Офис (депо)</Popup>
              </CircleMarker>

              {routesByEngineer.map(r => (
                <Polyline
                  key={r.eng}
                  positions={r.coords}
                  pathOptions={{ color: engineerColor(r.eng), weight: 3, opacity: 0.55 }}
                />
              ))}

              {plan.assigned.map(a => (
                <CircleMarker key={a.request_id} center={[a.lat, a.lon]} radius={a.risk ? 9 : 7}
                  pathOptions={{
                    color: a.risk ? '#F0B429' : engineerColor(a.engineer_id),
                    weight: a.risk ? 3 : 1,
                    fillColor: engineerColor(a.engineer_id),
                    fillOpacity: 0.85
                  }}>
                  <Popup>{a.request_id} → {a.engineer_id}<br />{a.arrival}<br />{a.reason}</Popup>
                </CircleMarker>
              ))}
              {plan.unassigned.map(u => (
                <CircleMarker key={u.request_id} center={[u.lat, u.lon]} radius={7}
                  pathOptions={{ color: '#E4483A', fillColor: '#E4483A', fillOpacity: 0.65 }}>
                  <Popup>{u.request_id} — не назначена<br />{u.reason}</Popup>
                </CircleMarker>
              ))}
            </>
          )}
        </MapContainer>

        <div className="map-attribution">© OpenStreetMap contributors</div>
      </div>
    </div>
  )
}

export default App
