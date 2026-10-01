# Audience Discovery & Distribution Roadmap

Crowdfunding DeepSearch should eventually help campaigns reach both institutional opportunities and ordinary people who may care about, share, or support a campaign.

## Product architecture

### 1. Opportunity DeepSearch
Find and verify charities, nonprofits, assistance programs, foundations, community resources, media opportunities, corporate programs, and other legitimate support channels.

### 2. Audience DeepSearch
Discover public places where relevant people already gather or discuss needs related to a campaign.

Candidate channel classes include:
- public community forums and discussion boards
- relevant public social communities and topic pages
- local and regional community resources
- blogs, newsletters, directories, and public resource lists
- podcasts, radio programs, creators, reporters, and human-interest media
- search-indexable campaign/resource directories
- public mutual-aid and community-support spaces where campaign sharing is permitted

Discovery should rank channels using evidence such as topic relevance, geographic relevance, audience fit, recent activity, posting/submission rules, and whether the channel appears legitimate.

### 3. Distribution & Outreach
Turn verified opportunities into actions.

Supported action modes:
- automatic submission only when an official API, submission mechanism, or site rules permit it
- assisted submission when human review or a manual step is required
- prepared outreach drafts for email, forms, community posts, media pitches, and other permitted channels
- outreach history, duplicate prevention, response tracking, and follow-up reminders

## Geographic expansion

Search scope should be selectable and expandable rather than locked to a campaign's city.

Suggested scopes:
1. Local
2. Regional
3. State/province
4. National
5. International
6. Worldwide
7. Automatic expansion

Automatic expansion should start with high-relevance local opportunities and progressively broaden the search when useful. Eligibility/service area matters more than headquarters location: an organization based elsewhere should remain eligible for discovery if it serves the campaign's location.

## Organic discovery intelligence

The system should identify legitimate ways to improve the chance that people encounter a campaign naturally, including relevant directories, public community resources, media coverage opportunities, searchable pages, and permitted sharing channels.

The system should distinguish reach from relevance. A smaller highly relevant community can be more useful than a large unrelated audience.

## Safety and quality boundaries

Crowdfunding DeepSearch should not:
- create fake supporters or fake identities
- manufacture likes, comments, shares, endorsements, or testimonials
- bypass CAPTCHAs, bans, access controls, or rate limits
- spam communities or repeatedly contact the same recipient
- disguise duplicate campaigns to mislead donors or platforms
- claim guaranteed donations, grants, reach, or campaign outcomes

The system should respect platform rules, permissions, rate limits, privacy expectations, and truthful disclosure requirements.

## Future dashboard

A later dashboard can separately track:
- institutional opportunities discovered
- public audience channels discovered
- media/creator opportunities
- submissions prepared
- submissions sent
- manual actions required
- responses
- follow-ups
- duplicates prevented
- search scope reached
- source verification status

## Development order

1. Stabilize current campaign analyzer and Opportunity DeepSearch.
2. Add configurable geographic search scope and automatic expansion.
3. Build Audience DeepSearch discovery and ranking.
4. Add channel-rule and submission-method detection.
5. Add assisted outreach generation.
6. Add compliant automatic submission where technically and contractually permitted.
7. Add durable tracking, deduplication, analytics, and follow-up workflows.

This roadmap is additive: the existing opportunity-discovery engine remains the foundation rather than being replaced.


## V1 implementation status

Audience DeepSearch V1 is now under active implementation.

The first backend planner defines four bounded public-web discovery lanes:
- Community Forums
- Local Media
- Creators & Podcasts
- Directories & Newsletters

Automatic scope progressively samples local, state/province, national, and worldwide discovery. Audience candidates use a separate stable tracking namespace from institutional opportunities. V1 discovery actions default to `review_required`; discovery does not imply permission to post, submit, or contact automatically.

Audience DeepSearch has now progressed beyond the original V1 checklist. The current implementation includes candidate normalization/ranking, channel-rule evidence, dedicated audience planning/preview/discovery APIs, frontend results and tracking, campaign-specific workspaces, permission-review freshness checks, assisted outreach drafts, same-site submission-route verification, assisted submission packets, response/follow-up states, CSV outreach-ledger export, durable automation execution metadata, idempotency/retry controls, and a registered-but-disabled SendGrid connector guarded by explicit authorization, deployment opt-in, compliance preflight, durable storage, deduplication, and rate limiting.

### Operations & Analytics implementation status

The operations/analytics layer is now implemented across both institutional opportunities and public-audience discovery:

1. Privacy-safe per-workspace aggregate snapshots are persisted without private notes, response-detail text, outreach-draft bodies, or recipient addresses.
2. Audience reporting tracks discovered, saved, reviewed, draft-ready, submission-ready, contacted, responded, closed, follow-up due, permission-review freshness, submission-handoff freshness, and duplicate discoveries merged.
3. Institutional reporting now has a complete saved → reviewed → contacted → responded → closed funnel plus due, 24h+ overdue, and next-7-day follow-up metrics.
4. Institutional and public-audience funnels remain separately reported in the campaign progress export and cross-workspace operations dashboard.
5. Workspace Operations provides a bounded 50-item action queue, prioritizes overdue before due before upcoming work, distinguishes institutional opportunities from audience leads, and reports queue composition without exposing private record content.
6. The privacy-safe Workspace Operations export has advanced to schema v8 and includes aggregate stale-action, duplicate-suppression, institutional-funnel, and action-queue metrics.
7. Automatic distribution remains disabled unless a registered connector passes all permission, compliance, deduplication, storage, explicit-authorization, and deployment gates.

### Operational quality implementation status

The operational-quality frontier is now substantially implemented:

1. Cross-workspace action-queue items deep-link back to the correct campaign workspace using only a sanitized workspace identifier in the URL.
2. Audience and institutional follow-up calculations share common due, 24h+ overdue, next-7-day upcoming, invalid-date, and closed-record semantics.
3. Regression guards protect the 50-item queue cap, seven-day horizon, closed-record exclusion, urgency ordering, privacy flags, and workspace navigation.
4. Workspace Operations surfaces Audience and institutional analytics snapshot freshness as current (<24h), stale (24h+), stale (7d+), pending, or invalid.
5. Institutional discovery reports confirmed/possible service-area evidence, detected application/contact routes, program evidence, and explicitly unverified eligibility-language signals; these are filterable without becoming eligibility claims.
6. The institutional tracking dashboard now correctly counts reviewed and closed states and exposes route/readiness counters.
7. Workspace Operations export schema has advanced to v8 while preserving separation between institutional and public-audience funnels.
8. Automation diagnostics expose non-secret connector/deployment readiness and trusted server-side SendGrid readiness. These checks never authorize or attempt a send.

### Current development frontier

The next additive phase is automation auditability and compliance hardening:

1. Make non-sending dry-run and execution-prerequisite outcomes easier to inspect and retain without storing recipient addresses or message bodies in analytics exports.
2. Surface durable-storage and rate-limit readiness as non-secret operational signals.
3. Strengthen regression coverage proving readiness/simulation checks cannot be mistaken for live-send authorization.
4. Improve blocker grouping so permission, compliance, deduplication, storage, rate-limit, and deployment failures are distinguishable.
5. Keep live automatic distribution disabled unless every existing connector, permission, compliance, deduplication, storage, rate-limit, deployment, and explicit user-authorization gate passes.

The production baseline is protected by GitHub backend checks and exact-commit Render deployment verification.
