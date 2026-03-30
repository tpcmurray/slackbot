# Heartbeat Tasks

## News Digest
- Schedule: Daily at 09:00 NST
- Action: Run news digest from news_summary.yaml
- Target: #general

## Quiet Channel Check
- Schedule: Every 4 hours during 10:00-22:00 NST
- Condition: No messages in #general for > 3 hours
- Action: Post a conversation starter, observation, or light roast
- Personality: Use personality.md tone
