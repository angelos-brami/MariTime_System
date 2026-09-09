"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import type {
  Account,
  AccountUser,
  AlertPreview,
  Corridor,
  DeliveryChannel,
  DeliveryDashboard,
  EventType,
  EventWorkspaceData,
  OpenEvent,
  WatchProfile,
} from "../types";

const corridors: { value: Corridor; label: string }[] = [
  { value: "hormuz_gulf", label: "Hormuz / Gulf" },
  { value: "red_sea_bem_suez", label: "Red Sea / BEM / Suez" },
  { value: "east_med", label: "East Mediterranean" },
  { value: "port_specific", label: "Port specific" },
];

const eventTypes: { value: EventType; label: string }[] = [
  { value: "security_incident", label: "Security incident" },
  { value: "port_disruption", label: "Port disruption" },
  { value: "labor_action", label: "Labor action" },
  { value: "regulatory_sanctions", label: "Regulatory / sanctions" },
  { value: "weather_hazard", label: "Weather hazard" },
  { value: "infrastructure", label: "Infrastructure" },
  { value: "insurance_market", label: "Insurance market" },
  { value: "navigation_warning", label: "Navigation warning" },
];

function label(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(value));
}

async function detail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

function toggleValue<T>(current: T[], value: T): T[] {
  return current.includes(value) ? current.filter((item) => item !== value) : [...current, value];
}

function channelObject(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? value as Record<string, unknown> : {};
}

function ChannelControl({
  user,
  reviewer,
  onUpdated,
}: {
  user: AccountUser;
  reviewer: string;
  onUpdated: (user: AccountUser) => void;
}) {
  const telegram = channelObject(user.channels.telegram);
  const whatsapp = channelObject(user.channels.whatsapp);
  const [emailEnabled, setEmailEnabled] = useState(user.channels.email !== false);
  const [telegramChatId, setTelegramChatId] = useState(String(telegram.chat_id ?? ""));
  const [whatsappEnabled, setWhatsappEnabled] = useState(whatsapp.enabled === true);
  const [whatsappPhone, setWhatsappPhone] = useState(String(whatsapp.phone ?? user.phone ?? ""));
  const [optInConfirmed, setOptInConfirmed] = useState(whatsapp.enabled === true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const save = async () => {
    setSaving(true); setMessage(null);
    try {
      const response = await fetch(
        `/api/console/users/${encodeURIComponent(user.id)}/channels`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            updated_by: reviewer,
            email_enabled: emailEnabled,
            telegram_chat_id: telegramChatId.trim() || null,
            whatsapp_enabled: whatsappEnabled,
            whatsapp_phone: whatsappPhone.trim() || null,
            whatsapp_opt_in_confirmed: optInConfirmed,
          }),
        },
      );
      if (!response.ok) throw new Error(await detail(response));
      onUpdated((await response.json()) as AccountUser);
      setMessage("Delivery channels saved");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Channel update failed");
    } finally { setSaving(false); }
  };

  return (
    <div className="channelControl">
      <label><input type="checkbox" checked={emailEnabled} onChange={(event) => setEmailEnabled(event.target.checked)} /><span>Email</span></label>
      <label><span>Telegram chat ID</span><input value={telegramChatId} onChange={(event) => setTelegramChatId(event.target.value)} /></label>
      <label><input type="checkbox" checked={whatsappEnabled} onChange={(event) => { setWhatsappEnabled(event.target.checked); if (!event.target.checked) setOptInConfirmed(false); }} /><span>WhatsApp</span></label>
      <label><span>WhatsApp phone</span><input value={whatsappPhone} onChange={(event) => setWhatsappPhone(event.target.value)} placeholder="+3069…" /></label>
      <label><input type="checkbox" checked={optInConfirmed} onChange={(event) => setOptInConfirmed(event.target.checked)} /><span>Customer opt-in confirmed</span></label>
      <button type="button" disabled={saving || !reviewer.trim() || (whatsappEnabled && (!whatsappPhone.trim() || !optInConfirmed))} onClick={() => void save()}>{saving ? "Saving…" : "Save channels"}</button>
      {message ? <small role="status">{message}</small> : null}
    </div>
  );
}

function PortalAccessControl({
  user,
  reviewer,
  onUpdated,
}: {
  user: AccountUser;
  reviewer: string;
  onUpdated: (user: AccountUser) => void;
}) {
  const [subject, setSubject] = useState(user.auth_subject ?? "");
  const [enabled, setEnabled] = useState(user.portal_enabled);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const response = await fetch(
        `/api/console/users/${encodeURIComponent(user.id)}/portal-access`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            updated_by: reviewer,
            auth_subject: subject.trim() || null,
            portal_enabled: enabled,
          }),
        },
      );
      if (!response.ok) throw new Error(await detail(response));
      const updated = (await response.json()) as AccountUser;
      onUpdated(updated);
      setMessage(updated.portal_enabled ? "Portal enabled" : "Portal disabled");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Portal access update failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="portalAccessControl">
      <label><span>Clerk user ID</span><input value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="user_…" /></label>
      <label className="portalAccessToggle"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span>Portal enabled</span></label>
      <button type="button" disabled={saving || !reviewer.trim() || (enabled && !subject.trim())} onClick={() => void save()}>{saving ? "Saving…" : "Save access"}</button>
      {message ? <small role="status">{message}</small> : null}
    </div>
  );
}

export default function AlertOperations() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [profiles, setProfiles] = useState<WatchProfile[]>([]);
  const [events, setEvents] = useState<OpenEvent[]>([]);
  const [dashboard, setDashboard] = useState<DeliveryDashboard | null>(null);
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [profileName, setProfileName] = useState("Primary operations watch");
  const [profileCorridors, setProfileCorridors] = useState<Corridor[]>([]);
  const [profileEventTypes, setProfileEventTypes] = useState<EventType[]>([]);
  const [minSeverity, setMinSeverity] = useState(2);
  const [ports, setPorts] = useState("");
  const [onboardingComplete, setOnboardingComplete] = useState(false);
  const [reviewer, setReviewer] = useState("desk-analyst");
  const [selectedEventId, setSelectedEventId] = useState("");
  const [selectedWorkspace, setSelectedWorkspace] = useState<EventWorkspaceData | null>(null);
  const [channels, setChannels] = useState<DeliveryChannel[]>(["email", "telegram"]);
  const [alertNote, setAlertNote] = useState("");
  const [alertPreview, setAlertPreview] = useState<AlertPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      fetch("/api/console/accounts", { cache: "no-store" }),
      fetch("/api/console/watch-profiles", { cache: "no-store" }),
      fetch("/api/console/events", { cache: "no-store" }),
      fetch("/api/console/alerts/delivery-dashboard", { cache: "no-store" }),
    ])
      .then(async ([accountResponse, profileResponse, eventResponse, dashboardResponse]) => {
        for (const response of [accountResponse, profileResponse, eventResponse, dashboardResponse]) {
          if (!response.ok) throw new Error(await detail(response));
        }
        return Promise.all([
          accountResponse.json() as Promise<Account[]>,
          profileResponse.json() as Promise<WatchProfile[]>,
          eventResponse.json() as Promise<OpenEvent[]>,
          dashboardResponse.json() as Promise<DeliveryDashboard>,
        ]);
      })
      .then(([nextAccounts, nextProfiles, nextEvents, nextDashboard]) => {
        if (cancelled) return;
        setAccounts(nextAccounts);
        setProfiles(nextProfiles);
        setEvents(nextEvents);
        setDashboard(nextDashboard);
        setSelectedAccountId(nextAccounts[0]?.id ?? "");
        setSelectedEventId(nextEvents[0]?.id ?? "");
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Alert operations could not load");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedEventId) return;
    let cancelled = false;
    void fetch(`/api/console/events/${encodeURIComponent(selectedEventId)}/workspace`, {
      cache: "no-store",
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(await detail(response));
        return (await response.json()) as EventWorkspaceData;
      })
      .then((workspace) => {
        if (!cancelled) {
          setSelectedWorkspace(workspace);
          setAlertPreview(null);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Event could not load");
      });
    return () => {
      cancelled = true;
    };
  }, [selectedEventId]);

  const selectedAccount = useMemo(
    () => accounts.find((account) => account.id === selectedAccountId) ?? null,
    [accounts, selectedAccountId],
  );
  const selectedProfiles = profiles.filter((profile) => profile.account_id === selectedAccountId);
  const updateAccountUser = (updated: AccountUser) => {
    setAccounts((current) => current.map((account) => ({
      ...account,
      users: account.users.map((user) => user.id === updated.id ? updated : user),
    })));
  };
  const latestVersion = selectedWorkspace?.versions[0] ?? null;

  const createProfile = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedAccountId || !onboardingComplete) {
      setError("Complete the customer onboarding call before activating a profile.");
      return;
    }
    if (!profileCorridors.length && !profileEventTypes.length && !ports.trim()) {
      setError("Select at least one corridor, event type, or port.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const now = new Date().toISOString();
      const response = await fetch("/api/console/watch-profiles", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          account_id: selectedAccountId,
          name: profileName,
          corridors: profileCorridors,
          event_types: profileEventTypes,
          min_severity: minSeverity,
          ports: ports.split(",").map((port) => port.trim()).filter(Boolean),
          custom_geojson: null,
          active: true,
          configured_by: reviewer,
          configured_with_customer_at: now,
        }),
      });
      if (!response.ok) throw new Error(await detail(response));
      const profile = (await response.json()) as WatchProfile;
      setProfiles((current) => [...current, profile]);
      setNotice(`${profile.name} activated after recorded customer onboarding.`);
      setProfileName("Primary operations watch");
      setProfileCorridors([]);
      setProfileEventTypes([]);
      setPorts("");
      setOnboardingComplete(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Watch profile could not be created");
    } finally {
      setSubmitting(false);
    }
  };

  const setProfileActive = async (profile: WatchProfile, active: boolean) => {
    setSubmitting(true);
    setError(null);
    try {
      const response = active
        ? await fetch(`/api/console/watch-profiles/${encodeURIComponent(profile.id)}`, {
            method: "PATCH",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              updated_by: reviewer,
              active: true,
              configured_by: reviewer,
              configured_with_customer_at: new Date().toISOString(),
            }),
          })
        : await fetch(
            `/api/console/watch-profiles/${encodeURIComponent(profile.id)}?reviewer=${encodeURIComponent(reviewer)}`,
            { method: "DELETE" },
          );
      if (!response.ok) throw new Error(await detail(response));
      setProfiles((current) =>
        current.map((candidate) =>
          candidate.id === profile.id ? { ...candidate, active } : candidate,
        ),
      );
      setNotice(`${profile.name} ${active ? "activated" : "paused"}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Profile status could not be changed");
    } finally {
      setSubmitting(false);
    }
  };

  const previewAudience = async () => {
    if (!latestVersion || !channels.length) {
      setError("Select a published event version and at least one channel.");
      return;
    }
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(
        `/api/console/event-versions/${encodeURIComponent(latestVersion.id)}/alerts/preview`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            channels,
            released_by: reviewer,
            note: alertNote || null,
          }),
        },
      );
      if (!response.ok) throw new Error(await detail(response));
      const preview = (await response.json()) as AlertPreview;
      setAlertPreview(preview);
      setNotice(
        preview.ready
          ? `${preview.user_count} users at ${preview.account_count} accounts are ready for release.`
          : "Audience preview contains blockers.",
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Audience preview could not be generated");
    } finally {
      setSubmitting(false);
    }
  };

  const releaseAlert = async () => {
    if (!latestVersion || !alertPreview?.ready) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/console/event-versions/${encodeURIComponent(latestVersion.id)}/alerts`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            channels,
            released_by: reviewer,
            note: alertNote || null,
            preview_hash: alertPreview.preview_hash,
          }),
        },
      );
      if (!response.ok) throw new Error(await detail(response));
      const receipt = (await response.json()) as { delivery_count: number; alert_id: string };
      setNotice(`${receipt.delivery_count} retry-safe deliveries queued · alert ${receipt.alert_id.slice(0, 8)}…`);
      setAlertPreview(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Alert could not be released");
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <main className="eventWorkspaceLoading">Loading alert operations…</main>;

  return (
    <main className="alertOpsShell">
      <header className="eventWorkspaceHeader">
        <div className="eventWorkspaceBrand">
          <span className="wordmarkMark">EM</span>
          <div><p className="eyebrow">Customer operations</p><h1>Alert control</h1></div>
        </div>
        <div className="eventHeaderMeta"><span>Human release</span><span>Outbound kill switch</span></div>
        <nav className="workspaceNav" aria-label="Console navigation">
          <Link href="/console">Triage</Link>
          <Link href="/console/events">Events</Link>
          <Link href="/console/alerts" aria-current="page">Alerts</Link>
          <Link href="/console/extraction">Extraction</Link>
          <Link href="/console/briefs">Brief</Link>
          <Link href="/console/quality">Quality</Link>
        </nav>
      </header>
      <section className="workspaceControlBar">
        <label><span>Desk analyst</span><input aria-label="Desk analyst" value={reviewer} onChange={(event) => { setReviewer(event.target.value); setAlertPreview(null); }} /></label>
        <div className="workspaceStats"><span>{accounts.length} accounts</span><span>{profiles.filter((profile) => profile.active).length} active profiles</span><span>{dashboard?.total ?? 0} deliveries</span></div>
      </section>
      {error ? <p className="consoleBanner consoleBanner--error" role="alert">{error}</p> : null}
      {notice ? <p className="consoleBanner" role="status">{notice}</p> : null}

      <section className="deliveryKpis" aria-label="Delivery SLA dashboard">
        <article><span>Publish → delivery p95</span><strong>{dashboard?.publish_to_delivery_p95_seconds?.toFixed(1) ?? "—"}s</strong><small>Target &lt;60 seconds</small></article>
        <article><span>Within SLA</span><strong>{dashboard?.within_60_seconds_percent?.toFixed(1) ?? "—"}%</strong><small>Provider-accepted deliveries</small></article>
        <article><span>Delivered</span><strong>{dashboard?.status_counts.delivered ?? 0}</strong><small>{dashboard?.channel_counts.email ?? 0} email · {dashboard?.channel_counts.telegram ?? 0} Telegram</small></article>
        <article><span>Queued / failed</span><strong>{dashboard?.status_counts.queued ?? 0} / {dashboard?.status_counts.failed ?? 0}</strong><small>Retries remain persisted</small></article>
      </section>

      <div className="alertOpsGrid">
        <aside className="accountRail" aria-label="Customer accounts">
          <div className="panelHeading"><div><p className="eyebrow">Onboarding</p><h2>Accounts</h2></div></div>
          {accounts.map((account) => (
            <button key={account.id} type="button" className={account.id === selectedAccountId ? "accountCard accountCard--active" : "accountCard"} onClick={() => setSelectedAccountId(account.id)}>
              <span>{label(account.tier)}</span><strong>{account.company}</strong><small>{account.users.length} recipients</small>
            </button>
          ))}
        </aside>

        <section className="profileWorkspace" aria-label="Watch profile onboarding">
          <div className="panelHeading"><div><p className="eyebrow">30-minute customer call</p><h2>{selectedAccount?.company ?? "Watch profile"}</h2></div><span className="foundationBadge">{selectedAccount ? label(selectedAccount.tier) : "No account"}</span></div>
          <div className="recipientStrip">
            {selectedAccount?.users.map((user) => (
              <article key={user.id}><strong>{user.email}</strong><span>{Object.keys(user.channels).map(label).join(" · ")}</span><ChannelControl user={user} reviewer={reviewer} onUpdated={updateAccountUser} /><PortalAccessControl user={user} reviewer={reviewer} onUpdated={updateAccountUser} /></article>
            ))}
          </div>
          <form className="profileForm" onSubmit={createProfile}>
            <label className="profileNameField"><span>Profile name</span><input value={profileName} onChange={(event) => setProfileName(event.target.value)} required /></label>
            <label><span>Minimum severity</span><select value={minSeverity} onChange={(event) => setMinSeverity(Number(event.target.value))}>{[1, 2, 3, 4].map((severity) => <option key={severity} value={severity}>Severity {severity}</option>)}</select></label>
            <fieldset><legend>Trading areas</legend><div className="profileChoiceGrid">{corridors.map((corridor) => <label key={corridor.value}><input type="checkbox" checked={profileCorridors.includes(corridor.value)} onChange={() => setProfileCorridors((current) => toggleValue(current, corridor.value))} /><span>{corridor.label}</span></label>)}</div></fieldset>
            <fieldset><legend>Event types</legend><div className="profileChoiceGrid profileChoiceGrid--types">{eventTypes.map((eventType) => <label key={eventType.value}><input type="checkbox" checked={profileEventTypes.includes(eventType.value)} onChange={() => setProfileEventTypes((current) => toggleValue(current, eventType.value))} /><span>{eventType.label}</span></label>)}</div></fieldset>
            <label className="profilePortsField"><span>Ports / UNLOCODEs</span><input value={ports} onChange={(event) => setPorts(event.target.value)} placeholder="EGPSD, GRPIR" /></label>
            <label className="onboardingCheck"><input type="checkbox" checked={onboardingComplete} onChange={(event) => setOnboardingComplete(event.target.checked)} /><span>Customer configured this profile with us and channel tests are complete.</span></label>
            <button className="profileSubmit" type="submit" disabled={submitting || !onboardingComplete}>Activate watch profile</button>
          </form>
          <div className="profileList">
            {selectedProfiles.map((profile) => (
              <article key={profile.id} className="profileCard"><div><span className={profile.active ? "profileState profileState--active" : "profileState"}>{profile.active ? "Active" : "Paused"}</span><strong>{profile.name}</strong><small>S{profile.min_severity}+ · {profile.corridors.map(label).join(", ") || "Any corridor"}</small></div><button type="button" disabled={submitting} onClick={() => void setProfileActive(profile, !profile.active)}>{profile.active ? "Pause" : "Activate"}</button></article>
            ))}
          </div>
        </section>

        <aside className="alertComposer" aria-label="Alert composer">
          <div className="panelHeading"><div><p className="eyebrow">Human release</p><h2>Alert audience</h2></div></div>
          <div className="alertComposerBody">
            <label><span>Published event</span><select aria-label="Published event" value={selectedEventId} onChange={(event) => { setSelectedEventId(event.target.value); setAlertPreview(null); }}>{events.map((event) => <option key={event.id} value={event.id}>S{event.severity} · {event.title}</option>)}</select></label>
            <div className="latestVersionCard"><span>Immutable version</span><strong>{latestVersion ? `Version ${latestVersion.version_no}` : "No published version"}</strong><code>{latestVersion?.content_hash.slice(0, 12) ?? "—"}</code></div>
            <fieldset><legend>Channels</legend>{(["email", "telegram", "whatsapp"] as DeliveryChannel[]).map((channel) => <label key={channel}><input type="checkbox" checked={channels.includes(channel)} onChange={() => { setChannels((current) => toggleValue(current, channel)); setAlertPreview(null); }} /><span>{label(channel)}</span></label>)}</fieldset>
            <label><span>Analyst note</span><textarea value={alertNote} onChange={(event) => { setAlertNote(event.target.value); setAlertPreview(null); }} /></label>
            <button className="audiencePreviewButton" type="button" disabled={submitting || !latestVersion} onClick={() => void previewAudience()}>Preview affected customers</button>
            {alertPreview ? <div className="audienceSummary"><strong>{Object.values(alertPreview.planned_deliveries).reduce((sum, count) => sum + count, 0)} deliveries</strong><span>{alertPreview.user_count} users · {alertPreview.account_count} accounts</span><small>{alertPreview.rules_version}</small></div> : null}
            <div className="audienceAccounts">
              {alertPreview?.accounts.map((account) => <article key={account.account_id} className={account.throttled ? "audienceAccount audienceAccount--throttled" : "audienceAccount"}><div><strong>{account.company}</strong><span>{label(account.tier)} · {account.alerts_today}/{account.daily_cap} today</span></div><b>{account.throttled ? "Throttled" : `${account.delivery_count} sends`}</b>{account.reason ? <small>{account.reason}</small> : null}</article>)}
            </div>
            <button className="releaseAlertButton" type="button" disabled={submitting || !alertPreview?.ready} onClick={() => void releaseAlert()}>Release alert</button>
          </div>
        </aside>
      </div>

      <section className="deliveryActivity" aria-label="Recent deliveries">
        <div className="panelHeading"><div><p className="eyebrow">Timestamped evidence</p><h2>Recent deliveries</h2></div><span>Provider acceptance is the v1 delivery timestamp</span></div>
        <div className="deliveryTable" role="table">
          <div role="row" className="deliveryTableHeader"><span>Event / account</span><span>Recipient</span><span>Channel</span><span>Status</span><span>Latency</span></div>
          {dashboard?.recent.map((delivery) => <div role="row" key={delivery.id}><span><strong>{delivery.event_title}</strong><small>{delivery.account_company}</small></span><span>{delivery.recipient}</span><span>{label(delivery.channel)}</span><span className={`deliveryStatus deliveryStatus--${delivery.status}`}>{label(delivery.status)}</span><span>{delivery.delivered_at ? `${Math.max(0, Math.round((new Date(delivery.delivered_at).getTime() - new Date(delivery.queued_at).getTime()) / 1000))}s` : "—"}<small>{formatTime(delivery.queued_at)}</small></span></div>)}
        </div>
      </section>
    </main>
  );
}
