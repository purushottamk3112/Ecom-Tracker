import React, { useState, useEffect, useRef } from 'react';
import { Activity, MapPin, Box, ShieldAlert, CheckCircle2, XCircle, Upload, FileSpreadsheet, Server, Database, Filter, RefreshCw, Search, Download, Clock, Zap, AlertTriangle, TrendingUp, Truck } from 'lucide-react';

// Zone → default pincode mapping
const ZONE_PINCODE_MAP = {
  'Delhi - Connaught Place': '110001',
  'Delhi - Vasant Kunj': '110070',
  'Gurgaon - Sector 56': '122011',
  'Noida - Sector 62': '201309',
  'Mumbai - Powai': '400076',
  'Bangalore - Indiranagar': '560038',
  'Pune - Wakad': '411057',
};

export default function App() {
  const [zones, setZones] = useState([]);
  const [selectedZone, setSelectedZone] = useState('');
  const [pincode, setPincode] = useState('411057');
  
  const [platforms, setPlatforms] = useState({ 
    Blinkit: true, 
    Zepto: true, 
    Swiggy: true,
    Amazon: false,
    'Flipkart Main': false,
    'Flipkart Grocery': false,
    'Flipkart Minutes': false,
  });

  const [inputMode, setInputMode] = useState('single');
  const [singleQuery, setSingleQuery] = useState('');
  const [file, setFile] = useState(null);
  
  const [data, setData] = useState([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [error, setError] = useState(null);
  const [elapsedTime, setElapsedTime] = useState(0);
  const timerRef = useRef(null);

  // Fetch available zones on load
  useEffect(() => {
    fetch('http://127.0.0.1:8000/api/zones')
      .then(res => res.json())
      .then(d => {
        setZones(d.zones);
        if(d.zones.length > 0) setSelectedZone(d.zones[0]);
      })
      .catch(() => setError("Backend not running. Start it with: python api.py"));
  }, []);

  // Auto-update pincode when zone changes
  useEffect(() => {
    if (selectedZone && ZONE_PINCODE_MAP[selectedZone]) {
      setPincode(ZONE_PINCODE_MAP[selectedZone]);
    }
  }, [selectedZone]);

  // Elapsed time counter during processing
  useEffect(() => {
    if (isProcessing) {
      setElapsedTime(0);
      timerRef.current = setInterval(() => setElapsedTime(t => t + 1), 1000);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [isProcessing]);

  const handlePlatformToggle = (platform) => {
    setPlatforms(prev => ({ ...prev, [platform]: !prev[platform] }));
  };

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) setFile(e.target.files[0]);
  };

  const formatTime = (seconds) => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  };

  const handleScrape = async (e) => {
    e.preventDefault();
    
    if (inputMode === 'bulk' && !file) {
      setError("Please select an Excel or CSV file first.");
      return;
    }
    if (inputMode === 'single' && !singleQuery.trim()) {
      setError("Please enter a product name, ASIN, or FSN.");
      return;
    }
    if (!pincode.trim() || pincode.length !== 6) {
      setError("Please enter a valid 6-digit Pincode.");
      return;
    }
    
    const activePlatforms = Object.keys(platforms).filter(p => platforms[p]);
    if (activePlatforms.length === 0) {
      setError("Please select at least one platform.");
      return;
    }

    setIsProcessing(true);
    setError(null);
    setData([]);

    const formData = new FormData();
    formData.append('zone', selectedZone);
    formData.append('pincode', pincode);
    formData.append('platforms', JSON.stringify(activePlatforms));
    formData.append('mode', inputMode);

    if (inputMode === 'bulk') {
      formData.append('file', file);
    } else {
      formData.append('query', singleQuery);
    }

    try {
      const response = await fetch('http://127.0.0.1:8000/api/bulk-inventory', {
        method: 'POST',
        body: formData,
      });
      
      const json = await response.json();
      if (!response.ok) throw new Error(json.detail || "Server Error");
      
      setData(json.data);
    } catch (err) {
      setError(err.message || "Cannot connect to backend. Ensure it is running.");
    } finally {
      setIsProcessing(false);
    }
  };

  const handleExportCSV = () => {
    window.open('http://127.0.0.1:8000/api/export-csv', '_blank');
  };

  // Metrics
  const instockCount = data.filter(d => d.status === 'instock').length;
  const oosCount = data.filter(d => d.status === 'oos').length;
  const notFoundCount = data.filter(d => d.status === 'not_found').length;
  const errorCount = data.filter(d => ['error', 'cloudflare_blocked'].includes(d.status)).length;

  // Platform badge config
  const platformStyle = {
    'Blinkit': 'bg-yellow-500/20 text-yellow-300 border-yellow-500/30',
    'Zepto': 'bg-purple-500/20 text-purple-300 border-purple-500/30',
    'Swiggy Instamart': 'bg-orange-500/20 text-orange-300 border-orange-500/30',
    'Amazon': 'bg-blue-500/20 text-blue-300 border-blue-500/30',
    'Flipkart Main': 'bg-sky-500/20 text-sky-300 border-sky-500/30',
    'Flipkart Grocery': 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
    'Flipkart Minutes': 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30',
  };

  const statusConfig = {
    instock:           { label: 'In Stock',      icon: <CheckCircle2 size={14}/>, cls: 'text-emerald-400 font-medium' },
    oos:               { label: 'Out of Stock',  icon: <XCircle size={14}/>,      cls: 'text-rose-400' },
    not_found:         { label: 'Not Found',     icon: <Search size={14}/>,       cls: 'text-slate-500' },
    cloudflare_blocked:{ label: 'WAF Blocked',   icon: <ShieldAlert size={12}/>,  cls: 'text-red-400 text-xs' },
    error:             { label: 'Error',         icon: <AlertTriangle size={12}/>,cls: 'text-red-500 text-xs' },
  };

  return (
    <div className="min-h-screen bg-slate-900 text-slate-200 font-sans p-6">
      <header className="flex flex-col md:flex-row justify-between items-start md:items-center mb-8 gap-4 border-b border-slate-800 pb-6">
        <div>
          <h1 className="text-3xl font-bold text-white flex items-center gap-3">
            <Activity className="text-indigo-400" size={32} />
            Omni-Track Enterprise
            <span className="text-xs bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 px-2 py-0.5 rounded-full ml-2 font-normal">v2.0</span>
          </h1>
          <p className="text-slate-400 mt-2 flex items-center gap-2 text-sm">
            <Zap size={14} className="text-amber-400"/>
            Playwright Browser Engine
            <span className="text-slate-600">|</span>
            <Database size={14} className="text-emerald-400"/>
            Network Interception + DOM Parsing
          </p>
        </div>
        {isProcessing && (
          <div className="flex items-center gap-3 bg-indigo-600/20 border border-indigo-500/30 px-4 py-2 rounded-lg">
            <RefreshCw size={16} className="animate-spin text-indigo-400" />
            <div>
              <span className="text-indigo-300 text-sm font-medium">Scraping in progress...</span>
              <span className="text-indigo-400 text-xs ml-2 flex items-center gap-1 inline-flex">
                <Clock size={12}/> {formatTime(elapsedTime)}
              </span>
            </div>
          </div>
        )}
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-8">
        
        {/* ─── Left Sidebar ─── */}
        <div className="lg:col-span-1 space-y-6">
          <div className="bg-slate-800 rounded-xl p-6 border border-slate-700 shadow-xl">
            <h2 className="text-lg font-semibold text-white mb-6 flex items-center gap-2">
              <Filter size={18} className="text-indigo-400"/> Scrape Parameters
            </h2>
            
            <form onSubmit={handleScrape} className="space-y-6">
              {/* Zone + Pincode */}
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-slate-400 mb-2">Q-Commerce Zone (Lat/Lon)</label>
                  <select 
                    value={selectedZone} 
                    onChange={(e) => setSelectedZone(e.target.value)} 
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-slate-200 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 text-sm"
                  >
                    {zones.length === 0 && <option>Pune - Wakad</option>}
                    {zones.map(z => <option key={z} value={z}>{z}</option>)}
                  </select>
                </div>
                
                <div>
                  <label className="block text-sm font-medium text-slate-400 mb-2">E-Commerce Pincode</label>
                  <input 
                    type="text" 
                    value={pincode}
                    onChange={(e) => setPincode(e.target.value)}
                    placeholder="e.g. 411057"
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-slate-200 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 text-sm"
                    maxLength={6}
                  />
                </div>
              </div>

              {/* Platform Toggles */}
              <div>
                <label className="block text-sm font-medium text-slate-400 mb-3">Target Platforms</label>
                <div className="space-y-1">
                  {/* Q-Commerce */}
                  <p className="text-xs text-slate-500 uppercase tracking-wider mt-1 mb-2">Q-Commerce (Network Intercept)</p>
                  <div className="grid grid-cols-2 gap-2 mb-3">
                    {['Blinkit', 'Zepto', 'Swiggy'].map(platform => (
                      <PlatformCheckbox key={platform} platform={platform} checked={platforms[platform]} onToggle={handlePlatformToggle} />
                    ))}
                  </div>
                  {/* E-Commerce */}
                  <p className="text-xs text-slate-500 uppercase tracking-wider mt-2 mb-2">E-Commerce (DOM Parsing)</p>
                  <div className="grid grid-cols-2 gap-2">
                    {['Amazon', 'Flipkart Main', 'Flipkart Grocery', 'Flipkart Minutes'].map(platform => (
                      <PlatformCheckbox key={platform} platform={platform} checked={platforms[platform]} onToggle={handlePlatformToggle} />
                    ))}
                  </div>
                </div>
              </div>

              {/* Mode Toggle */}
              <div className="pt-2">
                <div className="flex bg-slate-900 rounded-lg p-1 border border-slate-700">
                  <button type="button" onClick={() => setInputMode('single')}
                    className={`flex-1 py-2 text-sm font-medium rounded-md transition-all ${inputMode === 'single' ? 'bg-indigo-600 text-white shadow' : 'text-slate-400 hover:text-slate-200'}`}>
                    Single Query
                  </button>
                  <button type="button" onClick={() => setInputMode('bulk')}
                    className={`flex-1 py-2 text-sm font-medium rounded-md transition-all ${inputMode === 'bulk' ? 'bg-indigo-600 text-white shadow' : 'text-slate-400 hover:text-slate-200'}`}>
                    Bulk Upload
                  </button>
                </div>
              </div>

              {/* Input Area */}
              {inputMode === 'single' ? (
                <div>
                  <label className="block text-sm font-medium text-slate-400 mb-2">Target Product</label>
                  <div className="relative">
                    <Search className="absolute left-3 top-3.5 text-slate-500" size={16} />
                    <input 
                      type="text" 
                      value={singleQuery}
                      onChange={(e) => setSingleQuery(e.target.value)}
                      placeholder="Enter FSN, ASIN, or Product Name..."
                      className="w-full bg-slate-900 border border-slate-700 rounded-lg pl-9 pr-4 py-3 text-slate-200 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 text-sm"
                    />
                  </div>
                  <p className="text-xs text-slate-500 mt-1.5">Auto-detects ASIN (B0xxxxxxxxx), FSN, or product name</p>
                </div>
              ) : (
                <div>
                  <label className="block text-sm font-medium text-slate-400 mb-2">Target Data (CSV/XLSX)</label>
                  <label className="border-2 border-dashed border-slate-600 hover:border-indigo-500 bg-slate-900/50 rounded-lg p-4 flex flex-col items-center justify-center cursor-pointer transition-colors text-center">
                    <Upload size={20} className="text-slate-400 mb-2" />
                    <span className="text-sm text-slate-300 font-medium line-clamp-1">
                      {file ? file.name : "Click to Upload File"}
                    </span>
                    <span className="text-xs text-slate-500 mt-1">Headers: product, fsn, asin, query, name, sku</span>
                    <input type="file" accept=".csv, .xlsx, .xls" className="hidden" onChange={handleFileChange} />
                  </label>
                </div>
              )}

              <button 
                type="submit" 
                disabled={isProcessing}
                className={`w-full bg-indigo-600 hover:bg-indigo-700 text-white py-3 rounded-lg font-medium transition-colors shadow-lg flex justify-center items-center gap-2 ${isProcessing ? 'opacity-70 cursor-not-allowed' : ''}`}
              >
                {isProcessing ? <RefreshCw size={18} className="animate-spin" /> : <Server size={18} />}
                {isProcessing ? `Scraping... ${formatTime(elapsedTime)}` : 'Start Scrape Engine'}
              </button>
            </form>
          </div>

          {/* Engine Info Card */}
          <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50">
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-2">Scraping Engine</p>
            <div className="space-y-2 text-xs">
              <div className="flex justify-between text-slate-400">
                <span>Browser</span>
                <span className="text-emerald-400">Playwright Chromium</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Anti-Detection</span>
                <span className="text-emerald-400">Stealth Mode</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Concurrency</span>
                <span className="text-slate-300">5 pages max</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Rate Limiting</span>
                <span className="text-slate-300">1.5-5s delays</span>
              </div>
            </div>
          </div>
        </div>

        {/* ─── Right Area — Results ─── */}
        <div className="lg:col-span-3 space-y-6">
          
          {/* Metrics */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <MetricCard title="In Stock" value={instockCount} icon={<CheckCircle2 className="text-emerald-400" size={20}/>} color="emerald" />
            <MetricCard title="Out of Stock" value={oosCount} icon={<XCircle className="text-rose-400" size={20}/>} color="rose" />
            <MetricCard title="Not Found" value={notFoundCount} icon={<Search className="text-slate-400" size={20}/>} color="slate" />
            <MetricCard title="Blocked/Error" value={errorCount} icon={<ShieldAlert className="text-amber-400" size={20}/>} color="amber" />
          </div>

          {/* Results Table */}
          <div className="bg-slate-800 rounded-xl border border-slate-700 shadow-xl overflow-hidden min-h-[500px]">
            <div className="p-4 sm:p-6 border-b border-slate-700 bg-slate-800/50 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <FileSpreadsheet size={20} className="text-indigo-400"/>
                Inventory Results
              </h2>
              <div className="flex items-center gap-3">
                {data.length > 0 && (
                  <>
                    <span className="text-xs text-slate-400 bg-slate-900 px-3 py-1.5 rounded-full border border-slate-700">
                      {data.length} records {elapsedTime > 0 && `• ${formatTime(elapsedTime)}`}
                    </span>
                    <button 
                      onClick={handleExportCSV}
                      className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 rounded-lg text-xs font-medium transition-colors"
                    >
                      <Download size={14}/> Export CSV
                    </button>
                  </>
                )}
              </div>
            </div>
            
            {error ? (
               <div className="p-12 text-center">
                 <ShieldAlert className="text-rose-500 mx-auto mb-4" size={48} />
                 <p className="text-rose-400 max-w-md mx-auto">{error}</p>
               </div>
            ) : data.length === 0 && !isProcessing ? (
              <div className="flex flex-col items-center justify-center p-24 text-center">
                <Box className="text-slate-600 mb-4" size={64} />
                <h3 className="text-xl font-bold text-slate-400 mb-2">Awaiting Target Parameters</h3>
                <p className="text-slate-500 max-w-sm">Enter a product query or upload a catalog file to map against live inventory across all platforms.</p>
              </div>
            ) : isProcessing && data.length === 0 ? (
              <div className="flex flex-col items-center justify-center p-24 text-center">
                <RefreshCw className="text-indigo-400 mb-4 animate-spin" size={48} />
                <h3 className="text-xl font-bold text-slate-300 mb-2">Scraping in Progress</h3>
                <p className="text-slate-500 max-w-sm">Playwright is navigating real browsers to each platform. This takes 5-10s per query.</p>
                <div className="mt-4 flex items-center gap-2 text-indigo-400">
                  <Clock size={16}/>
                  <span className="text-lg font-mono font-bold">{formatTime(elapsedTime)}</span>
                </div>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="bg-slate-900/50 text-slate-400 text-xs border-b border-slate-700">
                      <th className="p-3 sm:p-4 font-medium">Query / FSN / ASIN</th>
                      <th className="p-3 sm:p-4 font-medium">Platform</th>
                      <th className="p-3 sm:p-4 font-medium">Matched Product</th>
                      <th className="p-3 sm:p-4 font-medium">Status</th>
                      <th className="p-3 sm:p-4 font-medium">Price</th>
                      <th className="p-3 sm:p-4 font-medium">Match %</th>
                      <th className="p-3 sm:p-4 font-medium">Delivery</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-700/50">
                    {data.map((row, i) => {
                      const st = statusConfig[row.status] || statusConfig.error;
                      const ps = platformStyle[row.platform] || 'bg-slate-500/20 text-slate-300 border-slate-500/30';
                      return (
                        <tr key={i} className="hover:bg-slate-700/20 transition-colors">
                          <td className="p-3 sm:p-4 text-slate-300 font-medium max-w-[160px] truncate text-sm" title={row.uploaded_query}>
                            {row.uploaded_query}
                          </td>
                          <td className="p-3 sm:p-4">
                            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-semibold border ${ps}`}>
                              {row.platform}
                            </span>
                          </td>
                          <td className="p-3 sm:p-4 text-slate-400 text-sm max-w-[200px] truncate" title={row.matched_product}>
                            {row.matched_product}
                          </td>
                          <td className="p-3 sm:p-4">
                            <span className={`flex items-center gap-1 ${st.cls}`}>
                              {st.icon} {st.label}
                            </span>
                          </td>
                          <td className="p-3 sm:p-4 text-slate-300 font-medium text-sm">{row.price}</td>
                          <td className="p-3 sm:p-4">
                            {row.match_score ? (
                              <span className={`text-xs font-mono ${row.match_score >= 80 ? 'text-emerald-400' : row.match_score >= 60 ? 'text-amber-400' : 'text-slate-500'}`}>
                                {row.match_score}%
                              </span>
                            ) : (
                              <span className="text-slate-600 text-xs">-</span>
                            )}
                          </td>
                          <td className="p-3 sm:p-4 text-xs text-slate-400 max-w-[150px] truncate" title={row.delivery_info || ''}>
                            {row.delivery_info ? (
                              <span className="flex items-center gap-1 text-emerald-400"><Truck size={12}/> {row.delivery_info}</span>
                            ) : '-'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}


function PlatformCheckbox({ platform, checked, onToggle }) {
  return (
    <label className="flex items-center gap-2 cursor-pointer group">
      <div 
        className={`w-4 h-4 rounded border flex items-center justify-center transition-colors ${checked ? 'bg-indigo-600 border-indigo-600' : 'bg-slate-900 border-slate-600 group-hover:border-slate-400'}`} 
        onClick={() => onToggle(platform)}
      >
        {checked && <CheckCircle2 size={12} className="text-white" />}
      </div>
      <span className="text-slate-300 text-xs font-medium truncate" title={platform} onClick={() => onToggle(platform)}>
        {platform}
      </span>
    </label>
  );
}


function MetricCard({ title, value, icon, color }) {
  return (
    <div className="bg-slate-800 p-4 sm:p-5 rounded-xl border border-slate-700 shadow-lg flex items-start justify-between">
      <div>
        <p className="text-slate-400 text-xs font-medium mb-1">{title}</p>
        <h3 className="text-2xl font-bold text-white">{value}</h3>
      </div>
      <div className="bg-slate-900 p-2.5 rounded-lg border border-slate-700">
        {icon}
      </div>
    </div>
  );
}