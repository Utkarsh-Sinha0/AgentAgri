import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  BookOpen,
  Brain,
  CheckCircle2,
  Database,
  FileSearch,
  Leaf,
  RefreshCw,
  Satellite,
  ShieldCheck,
  Sprout,
  Users,
} from 'lucide-react';

import { Badge } from './components/ui/badge';
import { Button } from './components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './components/ui/card';
import { Input } from './components/ui/input';
import { Tabs, TabsList, TabsTrigger } from './components/ui/tabs';
import './styles.css';

const tabs = [
  { id: 'clusters', label: 'Clusters', icon: AlertTriangle },
  { id: 'memory', label: 'Memory', icon: Brain },
  { id: 'sources', label: 'Sources', icon: FileSearch },
  { id: 'eval', label: 'Eval', icon: BarChart3 },
];

async function api(path) {
  const apiKey = localStorage.getItem('agrimesh_api_key');
  const headers = apiKey ? { 'X-AgriMesh-API-Key': apiKey } : {};
  const res = await fetch(path, { headers });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

async function apiPost(path, body) {
  const apiKey = localStorage.getItem('agrimesh_api_key');
  const headers = { 'Content-Type': 'application/json' };
  if (apiKey) headers['X-AgriMesh-API-Key'] = apiKey;
  const res = await fetch(path, { method: 'POST', headers, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

function App() {
  const [activeTab, setActiveTab] = useState('clusters');
  const [stats, setStats] = useState(null);
  const [clusters, setClusters] = useState([]);
  const [memory, setMemory] = useState([]);
  const [sources, setSources] = useState([]);
  const [evalData, setEvalData] = useState(null);
  const [apiKey, setApiKey] = useState(localStorage.getItem('agrimesh_api_key') || '');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  async function loadDashboard() {
    setLoading(true);
    setError('');
    try {
      const [statsData, clusterData, memoryData, sourceData, evalResult] = await Promise.all([
        api('/api/stats').catch((err) => ({ error: err.message })),
        api('/api/clusters').catch(() => ({ clusters: [] })),
        api('/api/memory/summaries').catch(() => ({ summaries: [] })),
        api('/api/sources').catch(() => ({ sources: [] })),
        api('/api/eval/latest').catch(() => null),
      ]);
      if (statsData?.error) setError(statsData.error);
      setStats(statsData?.error ? null : statsData);
      setClusters(clusterData?.clusters || []);
      setMemory(memoryData?.summaries || []);
      setSources(sourceData?.sources || []);
      setEvalData(evalResult);
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

  const readiness = useMemo(() => buildReadiness(stats, sources, memory, clusters), [stats, sources, memory, clusters]);

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand-block">
          <div className="brand-mark"><Leaf size={22} /></div>
          <div>
            <h1>AgriMesh V4.0</h1>
            <p>Evidence-first extension dashboard for the Telegram farming agent</p>
          </div>
        </div>
        <div className="operator-controls">
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

      <section className="metric-grid" aria-label="System overview">
        <Metric icon={Users} label="Farmers" value={stats?.farmers ?? 0} />
        <Metric icon={BookOpen} label="Advisories" value={stats?.advisories ?? 0} />
        <Metric icon={Brain} label="Memory atoms" value={stats?.memory_atoms ?? 0} />
        <Metric icon={FileSearch} label="Sources" value={stats?.sources ?? sources.length} />
        <Metric icon={Database} label="Wiki articles" value={stats?.wiki_articles ?? 0} />
      </section>

      {error && (
        <Card className="notice-card">
          <CardContent>
            <AlertTriangle size={18} />
            <span>{error}. Add the API key if production auth is enabled.</span>
          </CardContent>
        </Card>
      )}

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
          {tabs.map((tab) => (
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
      {!loading && activeTab === 'sources' && <SourceView sources={sources} />}
      {!loading && activeTab === 'eval' && <EvalView evalData={evalData} />}
    </main>
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

function ClusterView({ clusters, reviewCluster }) {
  const [broadcasts, setBroadcasts] = useState({});

  if (!clusters.length) {
    return (
      <Card>
        <CardContent className="empty-state">
          <CheckCircle2 size={26} />
          <span>No pending clusters. The review queue is clear.</span>
        </CardContent>
      </Card>
    );
  }

  return (
    <section className="list-grid">
      {clusters.map((cluster) => (
        <Card key={cluster.id}>
          <CardHeader>
            <div className="row-between">
              <CardTitle>{cluster.issue_category || 'Unclassified issue'}</CardTitle>
              <Badge variant={severityVariant(cluster.severity)}>{severityLabel(cluster.severity)}</Badge>
            </div>
            <CardDescription>
              {cluster.district} / {cluster.tehsil || 'unknown tehsil'} · {cluster.crop_name} · {cluster.farmer_count} farmers
            </CardDescription>
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
              <Button variant="destructive" onClick={() => reviewCluster(cluster.id, 'dismiss')}>
                Dismiss
              </Button>
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
            <CardDescription>
              {item.atom_count} atoms · {item.farmer_count} farmers · {Math.round((item.confidence || 0) * 100)}% confidence
            </CardDescription>
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

function SourceView({ sources }) {
  if (!sources.length) {
    return (
      <Card>
        <CardContent className="empty-state">
          <FileSearch size={24} />
          <span>No source registry rows found. Restart the API once to seed official sources.</span>
        </CardContent>
      </Card>
    );
  }

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
  if (!evalData || evalData.status === 'no_eval_yet') {
    return (
      <Card>
        <CardContent className="empty-state">
          <Activity size={24} />
          <span>No eval run yet. Run the eval harness before final demo recording.</span>
        </CardContent>
      </Card>
    );
  }

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

function buildReadiness(stats, sources, memory, clusters) {
  return [
    { label: 'Agent DB', ok: Boolean(stats), icon: Database },
    { label: 'Evidence registry', ok: sources.length >= 6, icon: FileSearch },
    { label: 'Living memory', ok: (stats?.memory_atoms || 0) > 0 || memory.length > 0, icon: Brain },
    { label: 'Cluster review', ok: Array.isArray(clusters), icon: ShieldCheck },
    { label: 'Satellite/field data', ok: (stats?.memory_atoms || 0) > 0, icon: Satellite },
  ];
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
