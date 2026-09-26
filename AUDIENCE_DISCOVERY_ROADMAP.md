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
