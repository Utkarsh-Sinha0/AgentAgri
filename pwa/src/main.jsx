import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  BookOpen,
  CheckCircle2,
  CloudRain,
  Database,
  Droplets,
  FileSearch,
  GitBranch,
  IndianRupee,
  Leaf,
  MapPinned,
  MessageSquareText,
  Moon,
  RefreshCw,
  Satellite,
  ShieldCheck,
  Sprout,
  Sun,
  Users,
} from 'lucide-react';

import { Badge } from './components/ui/badge';
import { Button } from './components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './components/ui/card';
import { Input } from './components/ui/input';
import { Tabs, TabsList, TabsTrigger } from './components/ui/tabs';
import './styles.css';

const extensionTabs = [
  { id: 'clusters', label: 'Clusters', icon: AlertTriangle },
  { id: 'memory', label: 'Memory', icon: BookOpen },
  { id: 'impact', label: 'Impact', icon: GitBranch },
  { id: 'sources', label: 'Sources', icon: FileSearch },
  { id: 'eval', label: 'Eval', icon: BarChart3 },
];

const farmerTabs = [
  { id: 'overview', label: 'Overview', icon: Leaf },
  { id: 'fields', label: 'Fields', icon: Sprout },
  { id: 'map', label: 'Map', icon: MapPinned },
  { id: 'money', label: 'Money', icon: IndianRupee },
  { id: 'memory', label: 'Memory', icon: MessageSquareText },
];

function dashboardCacheKey(farmerId = 'default') {
  return `agrimesh_farmer_dashboard_${farmerId}`;
}

async function api(path) {
  const apiKey = localStorage.getItem('agrimesh_api_key');
  const headers = apiKey ? { 'X-AgriMesh-API-Key': apiKey } : {};
  const res = await fetch(path, { headers });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

async function apiPost(path, body, method = 'POST') {
  const apiKey = localStorage.getItem('agrimesh_api_key');
  const headers = { 'Content-Type': 'application/json' };
  if (apiKey) headers['X-AgriMesh-API-Key'] = apiKey;
  const res = await fetch(path, { method, headers, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

function App() {
  const params = new URLSearchParams(window.location.search);
  const farmerFromUrl = params.get('farmer_id') || localStorage.getItem('agrimesh_farmer_id') || '';
  const phoneFromUrl = params.get('phone') || '';
  const [mode, setMode] = useState(params.get('mode') === 'extension' ? 'extension' : 'farmer');
  const [farmerTab, setFarmerTab] = useState('overview');
  const [activeTab, setActiveTab] = useState('clusters');
  const [stats, setStats] = useState(null);
  const [clusters, setClusters] = useState([]);
  const [memory, setMemory] = useState([]);
  const [impacts, setImpacts] = useState([]);
  const [sources, setSources] = useState([]);
  const [evalData, setEvalData] = useState(null);
  const [farmerDashboard, setFarmerDashboard] = useState(() => {
    const cached = localStorage.getItem(dashboardCacheKey(farmerFromUrl || 'default'));
    return cached ? JSON.parse(cached) : null;
  });
  const [apiKey, setApiKey] = useState(localStorage.getItem('agrimesh_api_key') || '');
  const [farmerId, setFarmerId] = useState(farmerFromUrl);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  async function loadDashboard() {
    setLoading(true);
    setError('');
    try {
      const farmerQuery = farmerId
        ? `?farmer_id=${encodeURIComponent(farmerId)}`
        : phoneFromUrl
          ? `?phone=${encodeURIComponent(phoneFromUrl)}`
          : '';
      const [farmerData, statsData, clusterData, memoryData, impactData, sourceData, evalResult] = await Promise.all([
        api(`/api/farmer-dashboard${farmerQuery}`).catch((err) => ({ error: err.message })),
        api('/api/stats').catch((err) => ({ error: err.message })),
        api('/api/clusters').catch(() => ({ clusters: [] })),
        api('/api/memory/summaries').catch(() => ({ summaries: [] })),
        api('/api/impact-network').catch(() => ({ impacts: [] })),
        api('/api/sources').catch(() => ({ sources: [] })),
        api('/api/eval/latest').catch(() => null),
      ]);
      if (statsData?.error) setError(statsData.error);
      if (!farmerData?.error) {
        setFarmerDashboard(farmerData);
        setFarmerId(farmerData.farmer?.id || farmerId);
        localStorage.setItem('agrimesh_farmer_id', farmerData.farmer?.id || farmerId);
        localStorage.setItem(dashboardCacheKey(farmerData.farmer?.id || 'default'), JSON.stringify(farmerData));
      }
      setStats(statsData?.error ? null : statsData);
      setClusters(clusterData?.clusters || []);
      setMemory(memoryData?.summaries || []);
      setImpacts(impactData?.impacts || []);
      setSources(sourceData?.sources || []);
      setEvalData(evalResult);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadDashboard();
  }, []);

  function saveApiKey(value) {
    setApiKey(value);
    if (value.trim()) {
      localStorage.setItem('agrimesh_api_key', value.trim());
    } else {
      localStorage.removeItem('agrimesh_api_key');
    }
  }

  async function reviewCluster(id, action, msg = '') {
    await apiPost(`/api/clusters/${id}/review`, {
      action,
      extension_worker_id: 'demo_extension_worker',
      broadcast_message: msg,
    });
    setClusters((current) => current.filter((cluster) => cluster.id !== id));
  }

  async function saveProfilePatch(patch) {
    if (!farmerDashboard?.farmer?.id) return;
    const optimistic = {
      ...farmerDashboard,
      profile: { ...farmerDashboard.profile, ...patch },
    };
    setFarmerDashboard(optimistic);
    localStorage.setItem(dashboardCacheKey(farmerDashboard.farmer.id), JSON.stringify(optimistic));
    const result = await apiPost(`/api/farmers/${farmerDashboard.farmer.id}/profile`, patch, 'PUT');
    setFarmerDashboard((current) => ({ ...current, profile: result.profile }));
  }

  const weatherMood = farmerDashboard?.weather_skin?.mood || 'sunny';
  const readiness = useMemo(() => buildReadiness(stats, sources, memory, clusters), [stats, sources, memory, clusters]);

  return (
    <main className={`app-shell weather-${weatherMood}`}>
      <div className="weather-canvas" aria-hidden="true">
        <span className="cloud cloud-a" />
        <span className="cloud cloud-b" />
        <span className="rain-layer" />
      </div>
      <section className="shell">
        <header className="topbar">
          <div className="brand-block">
            <div className="brand-mark"><Leaf size={22} /></div>
            <div>
              <h1>{mode === 'farmer' ? 'My AgriMesh Farm' : 'AgriMesh V4.0'}</h1>
              <p>{mode === 'farmer' ? farmerDashboard?.farmer?.name || 'Personal farm command center' : 'Evidence-first extension dashboard'}</p>
            </div>
          </div>
          <div className="operator-controls">
            <Button variant={mode === 'farmer' ? 'default' : 'secondary'} onClick={() => setMode('farmer')}>
              <Leaf size={16} />
              Farmer
            </Button>
            <Button variant={mode === 'extension' ? 'default' : 'secondary'} onClick={() => setMode('extension')}>
              <ShieldCheck size={16} />
              Extension
            </Button>
            <Input
              aria-label="API key"
              placeholder="API key"
              value={apiKey}
              onChange={(event) => saveApiKey(event.target.value)}
            />
            <Button variant="secondary" onClick={loadDashboard} disabled={loading}>
              <RefreshCw size={16} />
              Refresh
            </Button>
          </div>
        </header>

        {error && (
          <Card className="notice-card">
            <CardContent>
              <AlertTriangle size={18} />
              <span>{error}. Add the API key if production auth is enabled.</span>
            </CardContent>
          </Card>
        )}

        {mode === 'farmer' ? (
          <FarmerShell
            dashboard={farmerDashboard}
            loading={loading}
            activeTab={farmerTab}
            setActiveTab={setFarmerTab}
            saveProfilePatch={saveProfilePatch}
          />
        ) : (
          <ExtensionShell
            stats={stats}
            clusters={clusters}
            memory={memory}
            impacts={impacts}
            sources={sources}
            evalData={evalData}
            loading={loading}
            readiness={readiness}
            activeTab={activeTab}
            setActiveTab={setActiveTab}
            reviewCluster={reviewCluster}
          />
        )}
      </section>
    </main>
  );
}

function FarmerShell({ dashboard, loading, activeTab, setActiveTab, saveProfilePatch }) {
  if (loading && !dashboard) return <LoadingState />;
  if (!dashboard) return <EmptyMessage icon={Users} text="No farmer profile found. Start with /demo or /register in Telegram." />;

  return (
    <>
      <WeatherHero dashboard={dashboard} />
      <FarmerQuickStats dashboard={dashboard} />
      <Tabs>
        <TabsList>
          {farmerTabs.map((tab) => (
            <TabsTrigger key={tab.id} active={activeTab === tab.id} onClick={() => setActiveTab(tab.id)}>
              <tab.icon size={16} />
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {activeTab === 'overview' && <FarmerOverview dashboard={dashboard} saveProfilePatch={saveProfilePatch} />}
      {activeTab === 'fields' && <FieldView dashboard={dashboard} />}
      {activeTab === 'map' && <FarmerMapView dashboard={dashboard} />}
      {activeTab === 'money' && <MoneyView dashboard={dashboard} />}
      {activeTab === 'memory' && <FarmerMemoryView dashboard={dashboard} />}
    </>
  );
}

function WeatherHero({ dashboard }) {
  const skin = dashboard.weather_skin || {};
  const today = dashboard.weather?.forecast?.[0] || {};
  const Icon = skin.is_night ? Moon : today.rainfall_mm > 0 ? CloudRain : Sun;
  return (
    <section className="weather-hero">
      <div>
        <span className="eyebrow">{dashboard.farmer?.village || dashboard.farmer?.district} · {skin.is_night ? 'Night field view' : 'Day field view'}</span>
        <h2>{today.condition || 'field weather'} · {today.temp_max || '?'}° / {today.temp_min || '?'}°C</h2>
        <p>Humidity {today.humidity || 0}% · Rain {today.rainfall_mm || 0} mm · Wind {today.wind_kmh || 0} km/h</p>
      </div>
      <div className="hero-weather-icon">
        <Icon size={38} />
      </div>
    </section>
  );
}

function FarmerQuickStats({ dashboard }) {
  const activeField = dashboard.fields?.[0];
  const crop = activeField?.active_crop;
  return (
    <section className="metric-grid farmer-metrics" aria-label="Farmer overview">
      <Metric icon={Sprout} label="Active crop" value={crop?.crop_name || 'n/a'} />
      <Metric icon={Leaf} label="Stage" value={crop?.current_stage || 'n/a'} />
      <Metric icon={Satellite} label="NDVI" value={formatMetric(activeField?.ndvi_trend?.latest)} />
      <Metric icon={IndianRupee} label="Net P&L" value={`₹${Math.round(dashboard.finance?.net || 0).toLocaleString()}`} />
      <Metric icon={AlertTriangle} label="Nearby alerts" value={dashboard.clusters?.clusters?.length || 0} />
    </section>
  );
}

function FarmerOverview({ dashboard, saveProfilePatch }) {
  const advisories = dashboard.advisories || [];
  const latest = advisories[0];
  return (
    <section className="farmer-overview-grid">
      <Card className="wide-card">
        <CardHeader>
          <div className="row-between">
            <CardTitle>Next Best Actions</CardTitle>
            <Badge variant={riskVariant(latest?.risk_level)}>{latest?.risk_level || 'no advisory'}</Badge>
          </div>
          <CardDescription>{latest?.contextualization || 'Ask the Telegram agent about your field to generate a verified advisory.'}</CardDescription>
        </CardHeader>
        <CardContent>
          <ActionList items={latest?.actions_text || []} />
          <WarningList items={latest?.warnings_text || []} />
        </CardContent>
      </Card>
      <ProfileCoach dashboard={dashboard} saveProfilePatch={saveProfilePatch} />
      <Card>
        <CardHeader>
          <CardTitle>Weather Plan</CardTitle>
          <CardDescription>Five-day field forecast</CardDescription>
        </CardHeader>
        <CardContent>
          <ForecastStrip forecast={dashboard.weather?.forecast || []} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Market Signal</CardTitle>
          <CardDescription>{dashboard.mandi?.district} · {dashboard.mandi?.source}</CardDescription>
        </CardHeader>
        <CardContent>
          <MarketRows mandi={dashboard.mandi} />
        </CardContent>
      </Card>
    </section>
  );
}

function ProfileCoach({ dashboard, saveProfilePatch }) {
  const questions = dashboard.profile_questions || [];
  const missing = questions.filter((q) => q.is_missing).slice(0, 5);
  const profile = dashboard.profile || {};
  const [draft, setDraft] = useState({});
  const completeness = Math.round((profile.profile_completeness || 0) * 100);

  async function submit() {
    await saveProfilePatch(draft);
    setDraft({});
  }

  return (
    <Card>
      <CardHeader>
        <div className="row-between">
          <CardTitle>Profile Coach</CardTitle>
          <Badge variant={completeness > 70 ? 'success' : 'warning'}>{completeness}%</Badge>
        </div>
        <CardDescription>Answers sync to server and stay cached on this device.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="question-stack">
          {(missing.length ? missing : questions.slice(0, 4)).map((q) => (
            <label key={q.id} className="question-row">
              <span>{q.label}</span>
              {q.options?.length ? (
                <select value={draft[q.id] ?? q.value ?? ''} onChange={(event) => setDraft({ ...draft, [q.id]: event.target.value })}>
                  <option value="">Select</option>
                  {q.options.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
                </select>
              ) : (
                <Input
                  type={q.input_type === 'number' ? 'number' : 'text'}
                  value={draft[q.id] ?? q.value ?? ''}
                  onChange={(event) => setDraft({ ...draft, [q.id]: q.input_type === 'number' ? Number(event.target.value) : event.target.value })}
                />
              )}
            </label>
          ))}
        </div>
        <Button onClick={submit} disabled={!Object.keys(draft).length}>
          <CheckCircle2 size={16} />
          Save answers
        </Button>
      </CardContent>
    </Card>
  );
}

function FieldView({ dashboard }) {
  return (
    <section className="list-grid">
      {dashboard.fields?.map((field) => (
        <Card key={field.id}>
          <CardHeader>
            <div className="row-between">
              <CardTitle>{field.name || 'Field'}</CardTitle>
              <Badge variant={ndviVariant(field.ndvi_trend?.status)}>{field.ndvi_trend?.status || 'unknown'}</Badge>
            </div>
            <CardDescription>{field.area_acres} acres · {field.soil_type || 'soil unknown'} · {field.irrigation_type || 'water unknown'}</CardDescription>
          </CardHeader>
          <CardContent>
            <MiniChart points={(field.ndvi || []).map((item) => item.ndvi)} />
            <div className="task-list">
              {(field.tasks || []).slice(0, 5).map((task) => (
                <div className="task-row" key={task.id}>
                  <span>{task.completed ? 'Done' : 'Pending'}</span>
                  <strong>{task.task_name}</strong>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      ))}
    </section>
  );
}

function FarmerMapView({ dashboard }) {
  const [zoom, setZoom] = useState(12);
  const [selected, setSelected] = useState(null);
  const mapData = dashboard.clusters || {};
  const merged = useMemo(() => mergeClusters(mapData.clusters || [], zoom), [mapData, zoom]);

  return (
    <section className="map-layout">
      <Card className="map-card">
        <CardHeader>
          <div className="row-between">
            <CardTitle>Cluster Map</CardTitle>
            <Badge>{clusterLevel(zoom)}</Badge>
          </div>
          <CardDescription>Zoom out to merge field alerts into village, tehsil, district, and state clusters.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="zoom-row">
            <span>Zoom</span>
            <input type="range" min="4" max="16" value={zoom} onChange={(event) => setZoom(Number(event.target.value))} />
            <strong>{zoom}</strong>
          </div>
          <div className="cluster-map" role="img" aria-label="Farmer field and nearby alert clusters">
            <div className="map-grid" />
            <button className="field-pin" style={{ left: '50%', top: '50%' }} onClick={() => setSelected({ own: true, ...mapData.own_field })}>
              <Leaf size={14} />
            </button>
            {merged.map((cluster) => (
              <button
                key={cluster.id}
                className={`cluster-pin severity-${severityVariant(cluster.severity)}`}
                style={{ left: `${cluster.x}%`, top: `${cluster.y}%`, width: pinSize(cluster), height: pinSize(cluster) }}
                onClick={() => setSelected(cluster)}
              >
                {cluster.farmer_count}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>
      <ClusterInsightPanel selected={selected} dashboard={dashboard} />
    </section>
  );
}

function ClusterInsightPanel({ selected, dashboard }) {
  if (!selected) {
    return <EmptyMessage icon={MapPinned} text="Tap your field or a cluster to see farmer-specific local insight." />;
  }
  if (selected.own) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{selected.name || 'Your field'}</CardTitle>
          <CardDescription>{selected.soil_type || 'soil unknown'} · {selected.area_acres || '?'} acres</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="body-copy">This is your active field. Nearby cluster warnings are interpreted relative to this crop, soil, water source, and current stage.</p>
        </CardContent>
      </Card>
    );
  }
  const activeCrop = dashboard.fields?.[0]?.active_crop?.crop_name || selected.crop_name;
  return (
    <Card>
      <CardHeader>
        <div className="row-between">
          <CardTitle>{selected.issue_category}</CardTitle>
          <Badge variant={severityVariant(selected.severity)}>{severityLabel(selected.severity)}</Badge>
        </div>
        <CardDescription>{selected.level} cluster · {selected.farmer_count} farmers · {selected.crop_name}</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="body-copy">Relevant to your {activeCrop} field if symptoms match. Check leaves, water level, and weather before acting.</p>
        <ActionList items={selected.recommendations || [selected.broadcast_message, 'Compare your field symptoms before copying another farmer action.'].filter(Boolean)} />
      </CardContent>
    </Card>
  );
}

function MoneyView({ dashboard }) {
  const finance = dashboard.finance || {};
  const categories = Object.entries(finance.by_category || {});
  return (
    <section className="farmer-overview-grid">
      <Card>
        <CardHeader>
          <CardTitle>Season P&L</CardTitle>
          <CardDescription>{finance.entries || 0} synced finance entries</CardDescription>
        </CardHeader>
        <CardContent className="money-stack">
          <MetricLine label="Revenue" value={`₹${Math.round(finance.revenue || 0).toLocaleString()}`} />
          <MetricLine label="Expenses" value={`₹${Math.round(finance.expenses || 0).toLocaleString()}`} />
          <MetricLine label="Net" value={`₹${Math.round(finance.net || 0).toLocaleString()}`} strong />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Category Flow</CardTitle>
          <CardDescription>Revenue is positive, expenses are negative</CardDescription>
        </CardHeader>
        <CardContent>
          {categories.map(([key, value]) => (
            <div className="bar-row" key={key}>
              <span>{key}</span>
              <div><i style={{ width: `${Math.min(100, Math.abs(value) / 300)}%` }} /></div>
              <strong>{Math.round(value).toLocaleString()}</strong>
            </div>
          ))}
        </CardContent>
      </Card>
    </section>
  );
}

function FarmerMemoryView({ dashboard }) {
  return (
    <section className="list-grid">
      <Card>
        <CardHeader>
          <CardTitle>Conversation Continuity</CardTitle>
          <CardDescription>{dashboard.conversations?.length || 0} field conversations saved</CardDescription>
        </CardHeader>
        <CardContent>
          {(dashboard.conversations || []).map((thread) => (
            <div className="memory-item" key={thread.id}>
              <strong>{thread.title || 'Field conversation'} · {thread.turn_count} turns</strong>
              <span>{thread.running_summary || 'No summary yet'}</span>
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Living Memory</CardTitle>
          <CardDescription>{dashboard.memory?.atom_count || 0} remembered farm events</CardDescription>
        </CardHeader>
        <CardContent>
          {(dashboard.memory?.recent || []).map((item) => (
            <div className="memory-item" key={`${item.type}-${item.event_at}`}>
              <strong>{item.type}</strong>
              <span>{item.summary}</span>
            </div>
          ))}
        </CardContent>
      </Card>
      <ImpactView impacts={dashboard.impacts || []} stats={{ conversations: dashboard.conversations?.length || 0 }} />
    </section>
  );
}

function ExtensionShell({ stats, clusters, memory, impacts, sources, evalData, loading, readiness, activeTab, setActiveTab, reviewCluster }) {
  return (
    <>
      <section className="metric-grid" aria-label="System overview">
        <Metric icon={Users} label="Farmers" value={stats?.farmers ?? 0} />
        <Metric icon={BookOpen} label="Advisories" value={stats?.advisories ?? 0} />
        <Metric icon={MessageSquareText} label="Conversations" value={stats?.conversations ?? 0} />
        <Metric icon={GitBranch} label="Action impacts" value={stats?.action_impacts ?? impacts.length} />
        <Metric icon={FileSearch} label="Sources" value={stats?.sources ?? sources.length} />
        <Metric icon={Database} label="Wiki articles" value={stats?.wiki_articles ?? 0} />
      </section>
      <section className="readiness-strip">
        {readiness.map((item) => (
          <div className="readiness-item" key={item.label}>
            <item.icon size={16} />
            <span>{item.label}</span>
            <Badge variant={item.ok ? 'success' : 'warning'}>{item.ok ? 'ready' : 'check'}</Badge>
          </div>
        ))}
      </section>
      <Tabs>
        <TabsList>
          {extensionTabs.map((tab) => (
            <TabsTrigger key={tab.id} active={activeTab === tab.id} onClick={() => setActiveTab(tab.id)}>
              <tab.icon size={16} />
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {loading && <LoadingState />}
      {!loading && activeTab === 'clusters' && <ClusterView clusters={clusters} reviewCluster={reviewCluster} />}
      {!loading && activeTab === 'memory' && <MemoryView memory={memory} stats={stats} />}
      {!loading && activeTab === 'impact' && <ImpactView impacts={impacts} stats={stats} />}
      {!loading && activeTab === 'sources' && <SourceView sources={sources} />}
      {!loading && activeTab === 'eval' && <EvalView evalData={evalData} />}
    </>
  );
}

function Metric({ icon: Icon, label, value }) {
  return (
    <Card>
      <CardContent className="metric-card">
        <Icon size={18} />
        <div>
          <span>{label}</span>
          <strong>{String(value)}</strong>
        </div>
      </CardContent>
    </Card>
  );
}

function LoadingState() {
  return (
    <Card>
      <CardContent className="empty-state">
        <RefreshCw className="spin" size={22} />
        <span>Loading live AgriMesh state</span>
      </CardContent>
    </Card>
  );
}

function EmptyMessage({ icon: Icon, text }) {
  return (
    <Card>
      <CardContent className="empty-state">
        <Icon size={24} />
        <span>{text}</span>
      </CardContent>
    </Card>
  );
}

function ActionList({ items = [] }) {
  if (!items.length) return null;
  return (
    <ol className="action-list">
      {items.slice(0, 5).map((item) => <li key={item}>{item}</li>)}
    </ol>
  );
}

function WarningList({ items = [] }) {
  if (!items.length) return null;
  return (
    <ul className="warning-list">
      {items.slice(0, 4).map((item) => <li key={item}>{item}</li>)}
    </ul>
  );
}

function ForecastStrip({ forecast }) {
  return (
    <div className="forecast-strip">
      {forecast.map((day) => (
        <div key={day.date}>
          <strong>{day.date?.slice(5)}</strong>
          <span>{day.condition}</span>
          <b>{day.rainfall_mm} mm</b>
        </div>
      ))}
    </div>
  );
}

function MarketRows({ mandi }) {
  const rows = mandi?.prices || [];
  if (!rows.length) return <p className="body-copy">No market data for this crop yet.</p>;
  return rows.map((row) => {
    const latest = row.history?.[0] || {};
    return (
      <div className="market-row" key={row.type}>
        <span>{row.type}</span>
        <strong>₹{latest.modal}</strong>
        <Badge variant={latest.modal >= (mandi.msp || 0) ? 'success' : 'warning'}>MSP ₹{mandi.msp || 'n/a'}</Badge>
      </div>
    );
  });
}

function MiniChart({ points }) {
  const safe = points?.length ? points : [0.4, 0.45, 0.5, 0.52];
  const max = Math.max(...safe);
  const min = Math.min(...safe);
  const range = max - min || 1;
  const path = safe.map((point, idx) => {
    const x = (idx / Math.max(1, safe.length - 1)) * 100;
    const y = 90 - ((point - min) / range) * 70;
    return `${idx === 0 ? 'M' : 'L'} ${x} ${y}`;
  }).join(' ');
  return (
    <svg className="mini-chart" viewBox="0 0 100 100" preserveAspectRatio="none">
      <path d={path} />
    </svg>
  );
}

function ClusterView({ clusters, reviewCluster }) {
  const [broadcasts, setBroadcasts] = useState({});
  if (!clusters.length) return <EmptyMessage icon={CheckCircle2} text="No pending clusters. The review queue is clear." />;
  return (
    <section className="list-grid">
      {clusters.map((cluster) => (
        <Card key={cluster.id}>
          <CardHeader>
            <div className="row-between">
              <CardTitle>{cluster.issue_category || 'Unclassified issue'}</CardTitle>
              <Badge variant={severityVariant(cluster.severity)}>{severityLabel(cluster.severity)}</Badge>
            </div>
            <CardDescription>{cluster.district} / {cluster.tehsil || 'unknown tehsil'} · {cluster.crop_name} · {cluster.farmer_count} farmers</CardDescription>
          </CardHeader>
          <CardContent>
            <Input
              placeholder="Broadcast message in Hindi or English"
              value={broadcasts[cluster.id] || ''}
              onChange={(event) => setBroadcasts({ ...broadcasts, [cluster.id]: event.target.value })}
            />
            <div className="action-row">
              <Button onClick={() => reviewCluster(cluster.id, 'approve_broadcast', broadcasts[cluster.id] || `Alert: ${cluster.issue_category} in ${cluster.crop_name}. Check your crop today.`)}>
                <ShieldCheck size={16} />
                Approve
              </Button>
              <Button variant="ghost" onClick={() => reviewCluster(cluster.id, 'reviewed')}>
                <CheckCircle2 size={16} />
                Reviewed
              </Button>
              <Button variant="destructive" onClick={() => reviewCluster(cluster.id, 'dismiss')}>Dismiss</Button>
            </div>
          </CardContent>
        </Card>
      ))}
    </section>
  );
}

function MemoryView({ memory, stats }) {
  const scales = ['field', 'village', 'tehsil', 'district', 'state', 'national'];
  const fallback = scales.map((scale) => ({
    id: scale,
    scale,
    title: `${scale[0].toUpperCase()}${scale.slice(1)} memory`,
    summary_text: scale === 'field'
      ? 'Private field-level memory is active once observations, advisories, finance entries, and NDVI records are seeded.'
      : 'Regional summary appears after the privacy threshold is met.',
    key_patterns: [],
    atom_count: scale === 'field' ? stats?.memory_atoms || 0 : 0,
    farmer_count: scale === 'field' ? 1 : 0,
    confidence: scale === 'field' ? 0.8 : 0.3,
    is_public: scale !== 'field',
  }));
  const rows = memory.length ? memory : fallback;
  return (
    <section className="memory-grid">
      {rows.map((item) => (
        <Card key={item.id}>
          <CardHeader>
            <div className="row-between">
              <CardTitle>{item.title || item.scale}</CardTitle>
              <Badge variant={item.is_public ? 'outline' : 'default'}>{item.scale}</Badge>
            </div>
            <CardDescription>{item.atom_count} atoms · {item.farmer_count} farmers · {Math.round((item.confidence || 0) * 100)}% confidence</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="body-copy">{item.summary_text}</p>
            {!!item.key_patterns?.length && (
              <ul className="pattern-list">
                {item.key_patterns.slice(0, 4).map((pattern) => <li key={pattern}>{pattern}</li>)}
              </ul>
            )}
          </CardContent>
        </Card>
      ))}
    </section>
  );
}

function ImpactView({ impacts, stats }) {
  if (!impacts.length) return <EmptyMessage icon={GitBranch} text="No action impact nodes yet. They appear after verified advisories are generated." />;
  return (
    <section className="list-grid">
      {impacts.map((impact) => (
        <Card key={impact.id}>
          <CardHeader>
            <div className="row-between">
              <CardTitle>Action Impact</CardTitle>
              <Badge variant={impactVariant(impact.impact_level)}>{impact.impact_level}</Badge>
            </div>
            <CardDescription>{impact.time_horizon || 'time unknown'} · advisory {shortId(impact.advisory_id)}</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="body-copy strong-copy">{impact.action_text}</p>
            <p className="body-copy">{impact.expected_result}</p>
            <div className="impact-columns">
              <MiniList title="Needs" items={impact.dependencies} />
              <MiniList title="Risks" items={impact.risks} />
            </div>
          </CardContent>
        </Card>
      ))}
      <EmptyMessage icon={Database} text={`${stats?.conversations || 0} durable field conversations are available for follow-up context.`} />
    </section>
  );
}

function MiniList({ title, items = [] }) {
  return (
    <div>
      <strong>{title}</strong>
      <ul className="pattern-list">
        {items.slice(0, 4).map((item) => <li key={item}>{item}</li>)}
      </ul>
    </div>
  );
}

function SourceView({ sources }) {
  if (!sources.length) return <EmptyMessage icon={FileSearch} text="No source registry rows found. Restart the API once to seed official sources." />;
  return (
    <section className="source-table" aria-label="Evidence sources">
      {sources.map((source) => (
        <div className="source-row" key={source.id}>
          <div>
            <strong>{source.source_name}</strong>
            <span>{source.description || source.source_type}</span>
          </div>
          <Badge variant={source.is_official ? 'success' : 'outline'}>{source.is_official ? 'official' : source.trust_level}</Badge>
          <Badge variant={source.age_hours === null || source.age_hours > source.freshness_ttl_hours ? 'warning' : 'default'}>
            {source.age_hours === null ? 'unfetched' : `${source.age_hours}h old`}
          </Badge>
        </div>
      ))}
    </section>
  );
}

function EvalView({ evalData }) {
  if (!evalData || evalData.status === 'no_eval_yet') return <EmptyMessage icon={Activity} text="No eval run yet. Run the eval harness before final demo recording." />;
  const metrics = [
    ['Faithfulness', evalData.faithfulness_mean, 0.85, 'min'],
    ['Relevancy', evalData.answer_relevancy_mean, 0.8, 'min'],
    ['Schema validity', evalData.schema_validity_rate, 0.99, 'min'],
    ['Safety pass', evalData.safety_pass_rate, 0.99, 'min'],
    ['Latency p50', evalData.latency_p50_ms, 3500, 'max'],
    ['Latency p95', evalData.latency_p95_ms, 6000, 'max'],
  ];
  return (
    <section className="eval-grid">
      {metrics.map(([name, value, target, direction]) => (
        <Card key={name}>
          <CardContent className="eval-card">
            <span>{name}</span>
            <strong>{formatMetric(value)}</strong>
            <Badge variant={metricPass(value, target, direction) ? 'success' : 'warning'}>target {formatMetric(target)}</Badge>
          </CardContent>
        </Card>
      ))}
    </section>
  );
}

function MetricLine({ label, value, strong }) {
  return <div className={strong ? 'metric-line strong-copy' : 'metric-line'}><span>{label}</span><strong>{value}</strong></div>;
}

function buildReadiness(stats, sources, memory, clusters) {
  return [
    { label: 'Agent DB', ok: Boolean(stats), icon: Database },
    { label: 'Evidence registry', ok: sources.length >= 6, icon: FileSearch },
    { label: 'Living memory', ok: (stats?.memory_atoms || 0) > 0 || memory.length > 0, icon: BookOpen },
    { label: 'Conversation graph', ok: (stats?.conversations || 0) >= 0, icon: GitBranch },
    { label: 'Cluster review', ok: Array.isArray(clusters), icon: ShieldCheck },
    { label: 'Satellite/field data', ok: (stats?.memory_atoms || 0) > 0, icon: Satellite },
  ];
}

function mergeClusters(clusters, zoom) {
  const level = clusterLevel(zoom);
  const buckets = new Map();
  clusters.forEach((cluster) => {
    const key = cluster.merge_keys?.[level] || cluster.id;
    const existing = buckets.get(key) || { ...cluster, id: key, farmer_count: 0, severity: 0, items: [], level };
    existing.items.push(cluster);
    existing.farmer_count += cluster.farmer_count || 0;
    existing.severity = Math.max(existing.severity || 0, cluster.severity || 0);
    existing.lat = existing.items.reduce((sum, item) => sum + item.lat, 0) / existing.items.length;
    existing.lng = existing.items.reduce((sum, item) => sum + item.lng, 0) / existing.items.length;
    existing.x = Math.max(8, Math.min(92, 50 + (existing.lng - clusters[0].lng) * 900));
    existing.y = Math.max(8, Math.min(92, 50 - (existing.lat - clusters[0].lat) * 900));
    existing.recommendations = existing.items.map((item) => item.broadcast_message).filter(Boolean).slice(0, 3);
    buckets.set(key, existing);
  });
  return Array.from(buckets.values());
}

function clusterLevel(zoom) {
  if (zoom >= 14) return 'field';
  if (zoom >= 11) return 'village';
  if (zoom >= 8) return 'tehsil';
  if (zoom >= 5) return 'district';
  return 'state';
}

function pinSize(cluster) {
  return Math.max(32, Math.min(64, 28 + Math.sqrt(cluster.farmer_count || 1) * 7));
}

function riskVariant(value) {
  if (value === 'ESCALATE') return 'destructive';
  if (value === 'PREVENTIVE_ACTION') return 'warning';
  if (value === 'WATCH') return 'default';
  return 'outline';
}

function severityVariant(value) {
  if (value > 0.84) return 'destructive';
  if (value > 0.5) return 'warning';
  return 'success';
}

function severityLabel(value) {
  if (value > 0.84) return 'critical';
  if (value > 0.7) return 'high';
  if (value > 0.5) return 'medium';
  return 'low';
}

function ndviVariant(value) {
  if (value === 'declining') return 'warning';
  if (value === 'improving') return 'success';
  return 'default';
}

function impactVariant(level) {
  if (level === 'critical') return 'destructive';
  if (level === 'high') return 'warning';
  if (level === 'low') return 'outline';
  return 'default';
}

function shortId(value) {
  return value ? value.slice(0, 8) : 'unknown';
}

function metricPass(value, target, direction) {
  if (typeof value !== 'number') return false;
  return direction === 'max' ? value <= target : value >= target;
}

function formatMetric(value) {
  if (typeof value !== 'number') return 'n/a';
  if (value <= 1) return value.toFixed(3);
  return Math.round(value).toLocaleString();
}

createRoot(document.getElementById('root')).render(<App />);
