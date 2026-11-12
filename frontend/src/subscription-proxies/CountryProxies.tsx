import {useState} from 'react';
import {useMutation,useQuery,useQueryClient} from '@tanstack/react-query';
import {AlertTriangle,Check,LoaderCircle,Network,RefreshCw,Save,Trash2,Upload} from 'lucide-react';
import {Pagination,useList,useStored} from '../registration/lists/listing';
import {Button,ConfigNumberInput,ConfirmDialog,EmptyState,IconButton,Notice,StatusBadge,errorMessage,formatTime} from '../../../services/gpt-register/web/src/components/ConsoleUi';
import {AppSelect} from '../../../services/gpt-register/web/src/components/AppSelect';
import {IPRoyalFields,iproyalSettingsReady} from '../../../services/gpt-register/web/src/IPRoyalFields';
import type {IPRoyalSettings,ProxyCatalogSnapshot} from '../../../services/gpt-register/web/src/types';
import {endpoints} from './api';
import ProxyInventory from './Inventory';
const ISO_PROXY_COUNTRIES = 'AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW'.split(' ');
const regionNames = typeof Intl.DisplayNames === 'function' ? new Intl.DisplayNames(['zh-CN'], { type: 'region' }) : null;
const proxyCountryLabel = (code: string) => code === 'GLOBAL' ? '全球 · GLOBAL' : `${regionNames?.of(code) || code} · ${code}`;
const PROXY_COUNTRY_OPTIONS = ['GLOBAL', ...ISO_PROXY_COUNTRIES].map((code) => ({ code, label: proxyCountryLabel(code) }));
const PROXY_COUNTRY_SELECT_OPTIONS = PROXY_COUNTRY_OPTIONS.map((country) => ({ value: country.code, label: country.label }));
const PROXY_API_PROVIDER_OPTIONS = [
  { value: '711proxy', label: '711Proxy' },
  { value: 'b2proxy', label: 'B2Proxy' },
  { value: '1024proxy', label: '1024 Proxy' },
  { value: 'cliproxy', label: 'CLI Proxy' },
  { value: 'iproyal', label: 'IPRoyal' },
];
const DEFAULT_PROXY_API_COUNT = 600;
const PROXY_API_PROVIDER_META: Record<string, { label: string; sessionMinutes: number; regionalOnly: boolean; defaultProtocol: 'http' | 'socks5'; apiBaseUrl: string; defaultCount?: number; maxCount?: number }> = {
  '711proxy': { apiBaseUrl: 'http://global.rotgbapi.711proxy.com:8089/gen', label: '711Proxy', sessionMinutes: 180, regionalOnly: false, defaultProtocol: 'http', defaultCount: DEFAULT_PROXY_API_COUNT, maxCount: 900 },
  b2proxy: { apiBaseUrl: 'http://global.rrp.bestgo.work:8089/gen', label: 'B2Proxy', sessionMinutes: 60, regionalOnly: true, defaultProtocol: 'socks5', defaultCount: DEFAULT_PROXY_API_COUNT, maxCount: 900 },
  '1024proxy': { apiBaseUrl: 'https://white.1024proxy.com/white/api', label: '1024 Proxy', sessionMinutes: 120, regionalOnly: true, defaultProtocol: 'socks5', defaultCount: DEFAULT_PROXY_API_COUNT },
  cliproxy: { apiBaseUrl: 'https://api.cliproxy.io/white/api', label: 'CLI Proxy', sessionMinutes: 15, regionalOnly: true, defaultProtocol: 'http', defaultCount: DEFAULT_PROXY_API_COUNT },
  iproyal: { apiBaseUrl: 'https://resi-api.iproyal.com/v1', label: 'IPRoyal', sessionMinutes: 120, regionalOnly: false, defaultProtocol: 'http', defaultCount: DEFAULT_PROXY_API_COUNT },
};

function ProxyCountrySelect({ value, onValueChange, includeGlobal = true }: { value: string; onValueChange: (value: string) => void; includeGlobal?: boolean }) {
  return <AppSelect
    value={value}
    options={includeGlobal ? PROXY_COUNTRY_SELECT_OPTIONS : PROXY_COUNTRY_SELECT_OPTIONS.filter((option) => option.value !== 'GLOBAL')}
    onValueChange={onValueChange}
    ariaLabel="国家"
    searchable
    minMenuWidth={240}
    className="country-select"
  />;
}

function ManualProxyEntries({ country, onRemove }: { country: string; onRemove: (entryId: string) => void }) {
  const listing=useList('national-manual-'+country,'url'),{offset,setOffset}=listing;const [q,setQ]=useStored('national-manual-search-'+country,'');
  const entries = useQuery({ queryKey: ['proxies', 'entries', country, listing.params,q], queryFn: () => endpoints.proxyEntries(country, {...listing.params,q}) });
  return <div className="manual-proxy-list"><div className="table-toolbar"><input aria-label="搜索手工代理" placeholder="搜索地址" value={q} onChange={e=>{setQ(e.target.value);setOffset(0)}}/><Button onClick={()=>listing.sort('url')}>代理地址 {listing.sortDir==='asc'?'↑':'↓'}</Button></div>
    {entries.error && <Notice tone="danger">{errorMessage(entries.error)}</Notice>}
    {(entries.data?.items || []).map(entry => <div key={entry.id}><code>{entry.url}</code><IconButton label="删除手工代理" onClick={() => onRemove(entry.id)}><Trash2 size={14} /></IconButton></div>)}
    <Pagination size={listing.size} onSize={listing.setSize} total={entries.data?.total || 0} offset={offset} busy={entries.isFetching} onChange={setOffset} />
  </div>;
}

export default function CountryProxies() {
  const client = useQueryClient();
  const listing=useList('national-country-pools','countryCode');const [poolSearch,setPoolSearch]=useStored('national-country-search','');
  const proxies = useQuery({ queryKey: ['proxies', 'page',listing.params,poolSearch], queryFn: () => endpoints.proxiesPage({...listing.params,q:poolSearch}), refetchInterval: query => query.state.data?.operation ? 1500 : false });
  const [localContent, setLocalContent] = useState('');
  const [dynamicContent, setDynamicContent] = useState('');

  const [dynamicProtocol, setDynamicProtocol] = useState('http');

  const [dynamicMode, setDynamicMode] = useState<'append' | 'replace'>('append');
  const [quickProvider, setQuickProvider] = useState('711proxy');
  const [quickCountry, setQuickCountry] = useState('GLOBAL');
  const [providerDrafts, setProviderDrafts] = useState<Record<string, { count?: number; protocol?: string; sessionText?: string; apiBaseUrl?: string; iproyal?: IPRoyalSettings }>>({});
  const [localPreview, setLocalPreview] = useState<any>(null);
  const [dynamicPreview, setDynamicPreview] = useState<any>(null);
  const [dynamicOverrides, setDynamicOverrides] = useState<Array<{ line: number; countryCode: string; count?: number; protocol: string }>>([]);
  const [notice, setNotice] = useState<{ tone: string; text: string } | null>(null);
  const refresh = () => client.invalidateQueries({ queryKey: ['proxies'] });
  const importMutation = useMutation({
    mutationFn: async (body: any) => {
      const { autoPull, ...payload } = body;
      let sourceUrls = new Set<string>();
      if (payload.scope === 'dynamic' && autoPull) {
        const normalized = await endpoints.previewProxies(payload);
        if (normalized.invalid > 0) throw new Error(normalized.errors?.[0]?.message || '代理 API 参数无效');
        sourceUrls = new Set(normalized.items.filter((item: any) => item.kind === 'api').map((item: any) => item.url));
      }
      let snapshot = await endpoints.importProxies(payload) as ProxyCatalogSnapshot;
      const sourceIds = snapshot.dynamic?.countryPools
        .flatMap((pool) => pool.sources)
        .filter((source) => sourceUrls.has(source.url))
        .map((source) => source.id) || [];
      for (const sourceId of sourceIds) snapshot = await endpoints.pullProxySource(sourceId);
      return { snapshot, pulled: sourceIds.length };
    },
    onSuccess: (result, variables: any) => {
      if (variables.scope === 'dynamic') {
        setDynamicContent('');
        setDynamicPreview(null);
        setDynamicOverrides([]);
      } else {
        setLocalContent('');
        setLocalPreview(null);
      }
      void client.invalidateQueries({ queryKey: ['proxies'] });
      setNotice({ tone: 'success', text: result.pulled ? `已构建并拉取 ${result.pulled} 个代理 API 来源` : '代理资源已导入' });
      refresh();
    },
    onError: (error) => { setNotice({ tone: 'danger', text: errorMessage(error) }); refresh(); },
  });
  const quickProviderMeta = PROXY_API_PROVIDER_META[quickProvider];
  const savedSettings = proxies.data?.dynamic?.providerSettings?.[quickProvider];
  const draft = providerDrafts[quickProvider] || {};
  const quickCount = draft.count ?? savedSettings?.count ?? quickProviderMeta.defaultCount ?? DEFAULT_PROXY_API_COUNT;
  const apiBaseUrl = draft.apiBaseUrl ?? savedSettings?.apiBaseUrl ?? quickProviderMeta.apiBaseUrl;
  const quickProtocol = draft.protocol ?? savedSettings?.protocol ?? quickProviderMeta.defaultProtocol;
  const sessionText = draft.sessionText ?? String(savedSettings?.sessionMinutes ?? quickProviderMeta.sessionMinutes);
  const updateProviderDraft = (patch: typeof draft) => setProviderDrafts(current => ({ ...current, [quickProvider]: { ...current[quickProvider], ...patch } }));
  const sessionMinutes = Number(sessionText);
  const maxSessionMinutes = quickProvider === '1024proxy' ? 120 : quickProvider === 'iproyal' ? 10080 : 1440;
  const validSessionMinutes = !!sessionText.trim() && Number.isInteger(sessionMinutes) && sessionMinutes >= 1 && sessionMinutes <= maxSessionMinutes;
  const iproyalDraft = draft.iproyal || {};
  const providerReady = quickProvider !== 'iproyal' || iproyalSettingsReady(iproyalDraft, savedSettings?.iproyal);
  const providerPayload = { provider: quickProvider, count: quickCount, protocol: quickProtocol, sessionMinutes, apiBaseUrl, ...(quickProvider === 'iproyal' ? { iproyal: iproyalDraft } : {}) };
  const settingsRefresh = proxies.data?.dynamic?.settingsRefresh;
  const settingsRefreshing = settingsRefresh?.status === 'running' || !!proxies.data?.operation;
  const saveSettings = useMutation({
    mutationFn: (value: typeof providerPayload) => endpoints.saveProxyProviderSettings(value),
    onSuccess: async (data) => {
      await refresh();
      setProviderDrafts(previous => { const next = { ...previous }; delete next[data.provider]; return next; });
      setDynamicPreview(null);
      setNotice({ tone: 'success', text: `${PROXY_API_PROVIDER_META[data.provider].label} 配置已保存，正在更新该厂商已有国家池。` });
    },
    onError: error => setNotice({ tone: 'danger', text: errorMessage(error) }),
  });
  const quickPull = useMutation({
    mutationFn: () => endpoints.buildAndPullProxyApi({ ...providerPayload, countryCode: quickCountry }),
    onSuccess: async (snapshot) => {
      const actual = snapshot.dynamic.countryPools.find(pool => pool.countryCode === quickCountry)?.sources.find(source => source.provider === quickProvider)?.cachedCount;
      setNotice({ tone: 'success', text: `已拉取 ${quickCountry === 'GLOBAL' ? '全球' : quickCountry} 国家池${actual === undefined ? '' : ` ${actual} 条`}，目标 ${quickCount} 条` });
      await refresh();
      if (quickProvider === 'iproyal') setProviderDrafts(previous => ({ ...previous, iproyal: { ...previous.iproyal, iproyal: {} } }));
    },
    onError: (error) => { setNotice({ tone: 'danger', text: errorMessage(error) }); refresh(); },
  });
  const clear = useMutation({
    mutationFn: (scope: 'dynamic') => endpoints.clearProxies(scope),
    onSuccess: () => { setNotice({ tone: 'success', text: '代理池已清空' }); refresh(); },
    onError: (error) => { setNotice({ tone: 'danger', text: errorMessage(error) }); refresh(); },
  });
  const pull = useMutation({
    mutationFn: (sourceId: string) => endpoints.pullProxySource(sourceId),
    onSuccess: () => { setNotice({ tone: 'success', text: '代理 API 来源已刷新' }); refresh(); },
    onError: (error) => { setNotice({ tone: 'danger', text: errorMessage(error) }); refresh(); },
  });
  const all = useMutation({mutationFn:endpoints.pullAllProxySources,onSuccess:()=>{setNotice({tone:'success',text:'国家代理已刷新'});refresh()},onError:e=>setNotice({tone:'danger',text:errorMessage(e)})});
  const pullAll={pending:all.isPending,trigger:all};
  const removeDynamic = useMutation({
    mutationFn: endpoints.removeDynamicProxy,
    onSuccess: () => { setNotice({ tone: 'success', text: '删除完成' }); refresh(); },
    onError: (error) => { setNotice({ tone: 'danger', text: errorMessage(error) }); refresh(); },
  });
  const previewDynamic = async () => {
    try {
      const result = await endpoints.previewProxies({ content: dynamicContent, defaultProtocol: dynamicProtocol, scope: 'dynamic' });
      setDynamicPreview(result);
      setDynamicOverrides(result.items.map((item: any) => ({
        line: item.line,
        countryCode: item.countryCode,
        count: item.kind === 'api' ? item.count : undefined,
        protocol: item.protocol,
      })));
    } catch (error) { setNotice({ tone: 'danger', text: errorMessage(error) }); }
  };
  const updateOverride = (line: number, patch: Partial<{ countryCode: string; count: number; protocol: string }>) => {
    setDynamicOverrides((current) => current.map((item) => item.line === line ? { ...item, ...patch } : item));
  };
  const updateQuickProvider = (provider: string) => {
    if (saveSettings.isPending || quickPull.isPending) return;
    const meta = PROXY_API_PROVIDER_META[provider];
    setQuickProvider(provider);
    if (meta?.regionalOnly && quickCountry === 'GLOBAL') setQuickCountry('US');
  };
  const pools = proxies.data?.dynamic?.countryPools || [];
  const apiSourceCount = proxies.data?.apiSourceCount || 0;
  return <div className="proxy-page">
    {notice && <Notice tone={notice.tone} floating onClose={() => setNotice(null)} autoDismissToken={notice}>{notice.text}</Notice>}
    {proxies.error && <Notice tone="danger">{errorMessage(proxies.error)}<Button onClick={()=>refresh()}>重试</Button></Notice>}
    <div className="national-renewal"><span>独立续费默认国家</span><ProxyCountrySelect value={proxies.data?.settings?.renewal_country||'PH'} includeGlobal={false} onValueChange={code=>void endpoints.saveSettings(code).then(()=>refresh()).catch(e=>setNotice({tone:'danger',text:errorMessage(e)}))}/><small>每笔办理取一条，整单复用；结束后按粘性时长冷却。</small></div>
    <div className="proxy-split-workspace">
      <section className="proxy-pane proxy-dynamic-pane">
        <div className="proxy-pane-header"><div><span className="pane-kicker">DYNAMIC</span><h2>国家代理</h2><p>普通代理与代理商 API 可在同一批次粘贴</p></div><Button tone="quiet" title="按国家顺序逐个重新拉取全部 API 来源" icon={pullAll.pending ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />} onClick={() => pullAll.trigger.mutate()} disabled={settingsRefreshing || pullAll.pending || pull.isPending || !apiSourceCount}><span>{pullAll.pending ? '刷新中' : '刷新全部'}</span></Button><ConfirmDialog trigger={<IconButton label="清空国家池" disabled={clear.isPending || removeDynamic.isPending || settingsRefreshing || pull.isPending || pullAll.pending}>{clear.isPending ? <LoaderCircle className="spin" size={16} /> : <Trash2 size={16} />}</IconButton>} title="清空全部国家池" description="删除全部国家池及缓存，并释放 此处管理的 IPRoyal 白名单端口。有 IPRoyal 代理正在使用时会阻止删除；失败可重试。" danger confirmLabel="清空" onConfirm={() => clear.mutate('dynamic')} /></div>
        <div className="proxy-composer dynamic-composer">
          <div className="proxy-quick-builder">
            <div className="quick-field quick-provider-field"><span>代理商</span><AppSelect ariaLabel="代理商" value={quickProvider} onValueChange={updateQuickProvider} options={PROXY_API_PROVIDER_OPTIONS} /></div>
            <div className="quick-field"><span>国家</span><ProxyCountrySelect value={quickCountry} onValueChange={setQuickCountry} includeGlobal={!quickProviderMeta.regionalOnly} /></div>
            <label><span>数量</span><ConfigNumberInput min={1} max={quickProviderMeta.maxCount || (quickProvider === '711proxy' ? 900 : undefined)} value={quickCount} disabled={saveSettings.isPending || quickPull.isPending} onValueChange={count => updateProviderDraft({ count })} /></label>
            <div className="quick-field"><span>协议</span><AppSelect ariaLabel="协议" value={quickProtocol} disabled={saveSettings.isPending || quickPull.isPending} onValueChange={protocol => updateProviderDraft({ protocol })} options={[{ value: 'http', label: 'HTTP' }, { value: 'socks5', label: 'SOCKS5' }]} /></div>
            <label className="proxy-api-url-field"><span>API 地址（问号前）</span><input aria-label="API 地址（问号前）" type="text" autoCapitalize="none" autoCorrect="off" spellCheck={false} value={apiBaseUrl} placeholder={quickProviderMeta.apiBaseUrl} disabled={saveSettings.isPending || quickPull.isPending || proxies.isLoading || !!proxies.error} onChange={event => updateProviderDraft({ apiBaseUrl: event.target.value })} /><small>{quickProvider === 'iproyal' ? '使用官方白名单管理 API 根地址，系统自动配置国家和粘性会话。' : '填写完整的 http(s)://域名:端口/路径，后面的国家等参数由系统生成。'}</small></label>
            {quickProvider === 'iproyal' && <IPRoyalFields draft={iproyalDraft} saved={savedSettings?.iproyal} disabled={saveSettings.isPending || quickPull.isPending || proxies.isLoading || !!proxies.error} onChange={patch => updateProviderDraft({ iproyal: { ...iproyalDraft, ...patch } })} />}
            <div className="proxy-session-controls">
              <label><span>会话粘性时间（分钟）</span><input type="number" aria-label="会话粘性时间（分钟）" min={1} max={maxSessionMinutes} step={1} value={sessionText} disabled={saveSettings.isPending || quickPull.isPending || proxies.isLoading || !!proxies.error} onChange={event => updateProviderDraft({ sessionText: event.target.value })} /></label>
              <Button icon={saveSettings.isPending || settingsRefreshing ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />} disabled={saveSettings.isPending || settingsRefreshing || quickPull.isPending || pullAll.pending || pull.isPending || proxies.isLoading || !!proxies.error || !validSessionMinutes || !apiBaseUrl.trim() || !providerReady} onClick={() => saveSettings.mutate(providerPayload)}>{saveSettings.isPending ? '保存中' : settingsRefreshing ? '更新中' : '保存'}</Button>
              <Button tone="primary" icon={quickPull.isPending ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />} onClick={() => quickPull.mutate()} disabled={quickPull.isPending || saveSettings.isPending || settingsRefreshing || pullAll.pending || pull.isPending || proxies.isLoading || !!proxies.error || !validSessionMinutes || !apiBaseUrl.trim() || !providerReady || !(quickProviderMeta.regionalOnly ? /^[A-Z]{2}$/.test(quickCountry) : /^(GLOBAL|[A-Z]{2})$/.test(quickCountry))}>{quickPull.isPending ? '拉取中' : '构建并拉取'}</Button>
            </div>
            <p className="proxy-session-help">保存API 地址、条数、协议和粘性时间，并重新拉取该厂商全部已有国家池；各国家保持不变。构建并拉取仅更新所选国家。</p>
            {settingsRefresh && <div className="proxy-settings-progress" role="status">
              <strong>{PROXY_API_PROVIDER_META[settingsRefresh.provider]?.label || settingsRefresh.provider} · {settingsRefreshing ? `更新国家池 ${settingsRefresh.done}/${settingsRefresh.total}` : settingsRefresh.status === 'success' ? '保存并更新完成' : '参数已保存，部分代理未更新'}</strong>
              {settingsRefreshing ? <progress aria-label="国家池更新进度" value={settingsRefresh.done} max={settingsRefresh.total || 1} /> : <span>{settingsRefresh.total ? `成功 ${settingsRefresh.result?.succeeded ?? 0} · 失败 ${settingsRefresh.result?.failed ?? settingsRefresh.total} · 拉取 ${settingsRefresh.result?.pulled ?? 0} 条` : '暂无已有国家池，可选择国家后构建并拉取。'}</span>}
              {settingsRefresh.error && <span className="source-error">{settingsRefresh.error}</span>}
              {settingsRefresh.result?.results.filter(item => !item.ok).map(item => <span className="source-error" key={item.sourceId}>{item.countryCode}：{item.error}；保留原缓存，可再次保存重试。</span>)}
            </div>}
          </div>
          <div className="import-toolbar"><div className="segmented"><button className={dynamicMode === 'append' ? 'selected' : ''} onClick={() => setDynamicMode('append')}>追加</button><button className={dynamicMode === 'replace' ? 'selected' : ''} onClick={() => setDynamicMode('replace')}>替换</button></div><AppSelect className="import-protocol-select" ariaLabel="动态代理默认协议" value={dynamicProtocol} onValueChange={setDynamicProtocol} options={[{ value: 'http', label: '普通代理默认 HTTP' }, { value: 'socks5', label: '普通代理默认 SOCKS5' }]} /></div>
          <textarea value={dynamicContent} onChange={(event) => { setDynamicContent(event.target.value); setDynamicPreview(null); setDynamicOverrides([]); }} placeholder={'host:port:user:pass\nhttps://white.1024proxy.com/white/api?...\nhttps://api.cliproxy.io/white/api?...\nhttp://global.rotgbapi.711proxy.com:8089/gen?...'} />
          {dynamicPreview && <><div className="preview-result"><span><Check size={15} />有效 {dynamicPreview.valid}</span><span><AlertTriangle size={15} />错误 {dynamicPreview.invalid}</span><span>重复 {dynamicPreview.duplicate}</span></div><div className="dynamic-preview-list">{dynamicPreview.items.map((item: any) => { const override = dynamicOverrides.find((candidate) => candidate.line === item.line)!; const provider = PROXY_API_PROVIDER_META[item.provider]; return <div className="dynamic-preview-row" key={item.line}><span className="preview-line">{item.line}</span><div><strong>{item.kind === 'api' ? `${provider?.label || item.provider} API` : '代理'}</strong><code>{item.url}</code></div><input aria-label={`第 ${item.line} 行国家`} value={override?.countryCode || 'GLOBAL'} maxLength={6} onChange={(event) => updateOverride(item.line, { countryCode: event.target.value.toUpperCase() })} />{item.kind === 'api' && <ConfigNumberInput aria-label={`第 ${item.line} 行数量`} min={1} max={PROXY_API_PROVIDER_META[item.provider]?.maxCount || (item.provider === '711proxy' ? 900 : undefined)} value={override?.count || 1} onValueChange={(count) => updateOverride(item.line, { count })} />}<AppSelect ariaLabel={`第 ${item.line} 行协议`} value={override?.protocol || item.protocol} onValueChange={(protocol) => updateOverride(item.line, { protocol })} options={[{ value: 'http', label: 'HTTP' }, { value: 'socks5', label: 'SOCKS5' }]} /></div>; })}</div></>}
          <div className="composer-actions"><Button onClick={previewDynamic} disabled={!dynamicContent.trim()}>识别并预览</Button><Button tone="primary" icon={importMutation.isPending ? <LoaderCircle className="spin" size={16} /> : dynamicPreview?.items?.some((item: any) => item.kind === 'api') ? <RefreshCw size={16} /> : <Upload size={16} />} onClick={() => importMutation.mutate({ scope: 'dynamic', content: dynamicContent, mode: dynamicMode, defaultProtocol: dynamicProtocol, overrides: dynamicOverrides, autoPull: dynamicPreview?.items?.some((item: any) => item.kind === 'api') })} disabled={!dynamicPreview || dynamicPreview.invalid > 0 || importMutation.isPending}>{dynamicPreview?.items?.some((item: any) => item.kind === 'api') ? '导入并拉取' : '合并到国家池'}</Button></div>
        </div>
        <div className="proxy-pane-scroll country-pool-scroll">
          <div className="table-toolbar"><input aria-label="搜索国家或厂商" placeholder="搜索国家或厂商" value={poolSearch} onChange={e=>{setPoolSearch(e.target.value);listing.setOffset(0)}}/><Button onClick={()=>listing.sort('countryCode')}>国家 {listing.sortBy==='countryCode'?(listing.sortDir==='asc'?'↑':'↓'):'↕'}</Button><Button onClick={()=>listing.sort('total')}>代理数量 {listing.sortBy==='total'?(listing.sortDir==='asc'?'↑':'↓'):'↕'}</Button></div>{pools.length ? pools.map((pool) => <div className="country-pool" key={pool.countryCode}><div className="country-pool-heading"><div><span className="country-code">{pool.countryCode === 'GLOBAL' ? '全球' : pool.countryCode}</span><div><strong>{pool.total} 条代理</strong><small>{pool.manualCount} 手工 · {pool.sources.length} API</small></div></div><ConfirmDialog trigger={<IconButton label="删除此国家池" disabled={removeDynamic.isPending || clear.isPending || settingsRefreshing || pull.isPending || pullAll.pending}>{removeDynamic.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}</IconButton>} title={`删除 ${pool.countryCode} 代理池`} description="删除该国家池及缓存，并释放其中 此处管理的 IPRoyal 白名单端口。正在使用的 IPRoyal 代理会阻止删除；失败可重试。" danger confirmLabel="删除" onConfirm={() => removeDynamic.mutate({ countryCode: pool.countryCode })} /></div>{pool.sources.map((source) => { const provider = PROXY_API_PROVIDER_META[source.provider]; return <div className="proxy-source" key={source.id}><div className="source-meta"><div><strong>{provider?.label || source.provider} · {source.protocol.toUpperCase()}</strong><span>{source.count} 条 · 粘性 {source.sessionMinutes} 分钟</span></div><StatusBadge status={source.lastError ? 'failed' : source.lastPulledAt ? 'healthy' : 'unknown'} /></div><code>{source.url}</code><div className="source-footer"><span>{source.lastPulledAt ? `${formatTime(source.lastPulledAt)} · ${source.cachedCount} 条` : '尚未拉取'}</span>{source.lastNotice && <span className="source-notice">{source.lastNotice}</span>}{source.lastError && <span className="source-error">{source.lastError}{source.deletionPending ? '；删除完成前不会重新拉取' : '；当前保留上次缓存'}</span>}{source.lastPulledSettings && (source.lastPulledSettings.protocol !== source.protocol || source.lastPulledSettings.sessionMinutes !== source.sessionMinutes || source.lastPulledSettings.count !== source.count) && <span>上次缓存：{source.lastPulledSettings.protocol.toUpperCase()} · 粘性 {source.lastPulledSettings.sessionMinutes} 分钟</span>}<div><Button icon={pull.isPending && pull.variables === source.id ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />} onClick={() => pull.mutate(source.id)} disabled={settingsRefreshing || pull.isPending || removeDynamic.isPending || clear.isPending || source.deletionPending}>{source.lastPulledAt ? '刷新' : '拉取'}</Button><ConfirmDialog trigger={<IconButton label="删除 API 来源" disabled={removeDynamic.isPending || clear.isPending || settingsRefreshing || pull.isPending || pullAll.pending}>{removeDynamic.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}</IconButton>} title="删除 API 来源" description={source.provider === 'iproyal' ? '删除此来源及缓存，同时释放它在 IPRoyal 的白名单端口。使用中会阻止删除；失败保留记录，可再次点击删除。' : '只删除此 URL 及其上次拉取结果。'} danger confirmLabel="删除" onConfirm={() => removeDynamic.mutate({ countryCode: pool.countryCode, sourceId: source.id })} /></div></div></div>; })}{pool.manualCount > 0 && <ManualProxyEntries country={pool.countryCode} onRemove={(entryId) => removeDynamic.mutate({ countryCode: pool.countryCode, entryId })} />}</div>) : <EmptyState icon={<Network size={25} />} title="没有国家代理" detail="未识别国家的代理会自动归入全球池。" />}<Pagination offset={listing.offset} size={listing.size} onSize={listing.setSize} total={proxies.data?.total||0} busy={proxies.isFetching} onChange={listing.setOffset}/>
        </div>
      </section>
    </div>
    <ProxyInventory />
  </div>;
}

