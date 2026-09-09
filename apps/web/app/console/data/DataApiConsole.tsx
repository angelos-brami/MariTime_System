"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import type { Account, AccountApiKey, ApiScope, IssuedAccountApiKey } from "../types";

const scopes: { value: ApiScope; label: string }[] = [
  { value: "events:read", label: "Events" },
  { value: "versions:read", label: "Versions" },
  { value: "claims:read", label: "Claims" },
  { value: "calendar:read", label: "Calendar" },
  { value: "ais:read", label: "AIS context" },
];

async function detail(response: Response): Promise<string> {
  const payload = (await response.json().catch(() => ({}))) as { detail?: string };
  return payload.detail ?? `Request failed (${response.status})`;
}

export default function DataApiConsole() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [keys, setKeys] = useState<AccountApiKey[]>([]);
  const [accountId, setAccountId] = useState("");
  const [name, setName] = useState("Pilot integration");
  const [actor, setActor] = useState("desk-admin");
  const [selectedScopes, setSelectedScopes] = useState<ApiScope[]>(scopes.map((item) => item.value));
  const [issued, setIssued] = useState<IssuedAccountApiKey | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const dataAccounts = useMemo(() => accounts.filter((account) => account.tier === "data"), [accounts]);

  async function load() {
    const [accountsResponse, keysResponse] = await Promise.all([
      fetch("/api/console/accounts", { cache: "no-store" }),
      fetch("/api/console/api-keys", { cache: "no-store" }),
    ]);
    if (!accountsResponse.ok) throw new Error(await detail(accountsResponse));
    if (!keysResponse.ok) throw new Error(await detail(keysResponse));
    const nextAccounts = (await accountsResponse.json()) as Account[];
    setAccounts(nextAccounts);
    setKeys((await keysResponse.json()) as AccountApiKey[]);
    setAccountId((current) => current || nextAccounts.find((account) => account.tier === "data")?.id || "");
  }

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      fetch("/api/console/accounts", { cache: "no-store" }),
      fetch("/api/console/api-keys", { cache: "no-store" }),
    ]).then(async ([accountsResponse, keysResponse]) => {
      if (!accountsResponse.ok) throw new Error(await detail(accountsResponse));
      if (!keysResponse.ok) throw new Error(await detail(keysResponse));
      const nextAccounts = (await accountsResponse.json()) as Account[];
      const nextKeys = (await keysResponse.json()) as AccountApiKey[];
      if (cancelled) return;
      setAccounts(nextAccounts);
      setKeys(nextKeys);
      setAccountId(nextAccounts.find((account) => account.tier === "data")?.id || "");
    }).catch((reason: unknown) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : "Unable to load API keys");
    });
    return () => { cancelled = true; };
  }, []);

  function toggleScope(scope: ApiScope) {
    setSelectedScopes((current) => current.includes(scope) ? current.filter((item) => item !== scope) : [...current, scope]);
  }

  async function issueKey() {
    setBusy(true); setError(""); setNotice(""); setIssued(null);
    try {
      const response = await fetch("/api/console/api-keys", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ account_id: accountId, name, scopes: selectedScopes, created_by: actor, expires_at: null }),
      });
      if (!response.ok) throw new Error(await detail(response));
      const created = (await response.json()) as IssuedAccountApiKey;
      setIssued(created);
      setNotice("Key issued. Copy it now; the secret is never stored or shown again.");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "API key creation failed");
    } finally { setBusy(false); }
  }

  async function revokeKey(apiKey: AccountApiKey) {
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch(`/api/console/api-keys/${encodeURIComponent(apiKey.id)}/revoke`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ revoked_by: actor }),
      });
      if (!response.ok) throw new Error(await detail(response));
      setNotice(`Key ${apiKey.key_prefix} revoked.`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "API key revocation failed");
    } finally { setBusy(false); }
  }

  return (
    <main className="dataApiShell">
      <header className="eventWorkspaceHeader">
        <div className="eventWorkspaceBrand"><span className="wordmarkMark">EM</span><div><p className="eyebrow">Stage 3 seed</p><h1>Read-only Data API</h1></div></div>
        <nav className="workspaceNav" aria-label="Console navigation"><Link href="/console">Triage</Link><Link href="/console/events">Events</Link><Link href="/console/extraction">Extraction</Link><Link href="/console/alerts">Alerts</Link><Link href="/console/briefs">Brief</Link><Link href="/console/quality">Quality</Link><Link href="/console/data" aria-current="page">Data API</Link></nav>
      </header>
      {error ? <p className="consoleBanner consoleBanner--error" role="alert">{error}</p> : null}
      {notice ? <p className="consoleBanner" role="status">{notice}</p> : null}
      <section className="dataApiIntro"><div><p className="eyebrow">Bearer keys · stable IDs</p><h2>Account-scoped access</h2><p>Only active Data-tier accounts can receive keys. Each key carries explicit read scopes and its secret is returned once.</p></div><a href="/api/console/openapi">OpenAPI schema</a></section>
      {!dataAccounts.length ? <p className="consoleBanner consoleBanner--error">No Data-tier account is configured. Upgrade an account before issuing a key.</p> : null}
      <div className="dataApiGrid">
        <section className="qualityPanel">
          <div className="panelHeading"><div><p className="eyebrow">One-time secret</p><h2>Issue key</h2></div></div>
          <label><span>Named administrator</span><input value={actor} onChange={(event) => setActor(event.target.value)} /></label>
          <label><span>Data account</span><select value={accountId} onChange={(event) => setAccountId(event.target.value)}>{dataAccounts.map((account) => <option key={account.id} value={account.id}>{account.company}</option>)}</select></label>
          <label><span>Key name</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
          <fieldset><legend>Read scopes</legend>{scopes.map((scope) => <label className="scopeCheck" key={scope.value}><input type="checkbox" checked={selectedScopes.includes(scope.value)} onChange={() => toggleScope(scope.value)} /> {scope.label}</label>)}</fieldset>
          <button type="button" disabled={busy || !accountId || !name.trim() || !actor.trim() || !selectedScopes.length} onClick={() => void issueKey()}>Issue API key</button>
          {issued ? <div className="apiSecret"><strong>Copy now</strong><code>{issued.api_key}</code><button type="button" onClick={() => void navigator.clipboard.writeText(issued.api_key)}>Copy key</button></div> : null}
        </section>
        <section className="dataKeyLedger">
          <div className="panelHeading"><div><p className="eyebrow">Revocable credentials</p><h2>Key ledger</h2></div><span>{keys.length}</span></div>
          {keys.map((key) => <article key={key.id}><div><strong>{key.name}</strong><code>{key.key_prefix}</code></div><span>{key.scopes_json.join(" · ")}</span><small>Created {new Date(key.created_at).toLocaleString("en-GB")}{key.last_used_at ? ` · used ${new Date(key.last_used_at).toLocaleString("en-GB")}` : " · never used"}</small>{key.revoked_at ? <em>Revoked by {key.revoked_by}</em> : <button type="button" disabled={busy} onClick={() => void revokeKey(key)}>Revoke</button>}</article>)}
          {!keys.length ? <p className="portalEmpty">No API keys have been issued.</p> : null}
        </section>
      </div>
    </main>
  );
}
