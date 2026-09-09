import type { DeliveryChannel } from "@/app/console/types";
import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { demoAccounts, demoWatchProfiles, demoWorkspace } from "@/lib/console-demo";
import { readJsonRequest } from "@/lib/json-request";

export async function POST(
  request: Request,
  context: { params: Promise<{ versionId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<{ channels: DeliveryChannel[] }>(
    request,
    32_768,
    "Alert preview",
  );
  if (!parsed.ok) return parsed.response;
  const { body, payload } = parsed;
  const { versionId } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO !== "true") {
    return forwardDeskRequest(
      `/api/v1/event-versions/${encodeURIComponent(versionId)}/alerts/preview`,
      { method: "POST", body },
    );
  }
  const accounts = demoAccounts.map((account, index) => {
    const profile = demoWatchProfiles.find((candidate) => candidate.account_id === account.id);
    const throttled = account.tier === "watch";
    const enabledDeliveries = throttled
      ? 0
      : account.users.reduce(
          (count, user) =>
            count +
            payload.channels.filter((channel) => {
              const config = user.channels[channel];
              return Boolean(config && (config === true || (typeof config === "object" && config !== null)));
            }).length,
          0,
        );
    return {
      account_id: account.id,
      company: account.company,
      tier: account.tier,
      matched_profile_ids: profile ? [profile.id] : [],
      user_count: throttled ? 0 : account.users.length,
      delivery_count: enabledDeliveries,
      alerts_today: index === 2 ? 3 : index + 1,
      daily_cap: account.tier === "watch" ? 3 : account.tier === "desk" ? 8 : 12,
      throttled,
      reason: throttled ? "Daily watch cap of 3 reached" : null,
    };
  });
  const deliverable = accounts.filter((account) => !account.throttled);
  const planned = Object.fromEntries(
    payload.channels.map((channel) => [
      channel,
      deliverable.reduce((count, account) => {
        const source = demoAccounts.find((candidate) => candidate.id === account.account_id);
        return (
          count +
          (source?.users.filter((user) => {
            const config = user.channels[channel];
            return Boolean(config && (config === true || (typeof config === "object" && config !== null)));
          }).length ?? 0)
        );
      }, 0),
    ]),
  );
  return Response.json({
    event_version_id: versionId,
    event_id: demoWorkspace.id,
    title: demoWorkspace.title,
    severity: demoWorkspace.severity,
    rules_version: "alert-rules-v1",
    matched_profiles: demoWatchProfiles.length,
    account_count: accounts.length,
    user_count: new Set(
      deliverable.flatMap((account) =>
        demoAccounts.find((candidate) => candidate.id === account.account_id)?.users.map((user) => user.id) ?? [],
      ),
    ).size,
    planned_deliveries: planned,
    accounts,
    blockers: [],
    ready: Object.values(planned).reduce((sum, count) => sum + count, 0) > 0,
    preview_hash: "a".repeat(64),
  });
}
