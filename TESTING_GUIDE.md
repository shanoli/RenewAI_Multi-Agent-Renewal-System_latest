# Quick Test Guide - RenewAI Dashboard Fixes

## How to Verify Each Fix

### 1. 🔄 Dynamic Channel Distribution
**Location:** Overview Tab → "Channel Distribution (Last 24h)" card

**How to Test:**
1. Open the Overview tab
2. You should see "📧 Email", "💬 WhatsApp", "📞 Voice" with **dynamic counts** (no longer hardcoded 612, 341, 71)
3. Trigger some renewal workflows via the Policies tab
4. Reload the Overview tab - counts should update
5. **Expected:** Percentages should sum to 100%, colors should match channels

**What Changed:**
- Before: Always showed Email=612, WhatsApp=341, Voice=71
- After: Shows actual channel usage counts from `/dashboard/overview` API

---

### 2. 📊 Dynamic Renewal Rates
**Location:** Overview Tab → "Renewal Rate by Segment" card

**How to Test:**
1. Open the Overview tab
2. You should see segment renewal rates with **calculated percentages** (no longer hardcoded 82%, 74%, 61%)
3. Create test policies across different segments with different statuses
4. Reload the tab - percentages should update based on actual ACTIVE/PAID vs LAPSED policies
5. **Expected:** Rates = (ACTIVE + PAID policies) / (total policies in segment) × 100%

**What Changed:**
- Before: Always showed HNI=82%, Premium=74%, Standard=61%
- After: Shows actual renewal rate per segment calculated from `/dashboard/policies` data

---

### 3. ⚡ Real-time Recent Activity
**Location:** Overview Tab → "Recent Activity" table

**How to Test:**
1. Open the Overview tab
2. The Recent Activity table should show **real data** (no longer dummy POL-2847, POL-1923, etc.)
3. Trigger a renewal workflow from the Policies tab
4. Go back to Overview - new activity should appear in the table
5. Check that agent names and action types match what happened
6. **Expected:** Activity timestamps should show "just now", "2m ago", etc.

**What Changed:**
- Before: Always showed 5 hardcoded dummy rows
- After: Shows actual agent logs from `/dashboard/recent-activity` endpoint, updates in real-time

---

### 4. 🔍 Working Segment & Status Filters
**Location:** Policies Tab → Filter dropdowns

**How to Test:**
1. Open Policies tab (see full list load)
2. Click "All Segments" dropdown → select "Premium"
3. **Expected:** Only Premium segment policies show
4. Click "All Statuses" dropdown → select "ACTIVE"
5. **Expected:** Only ACTIVE policies show (combined with segment filter)
6. Type in search box: type a policy ID or customer name
7. **Expected:** Results filter in real-time
8. Change filters again - table should update immediately
9. **Expected:** No "function not defined" errors in console

**What Changed:**
- Before: Dropdown selections had no effect (filterPolicies() function didn't exist)
- After: filterPolicies() function works, filters happen client-side in real-time

---

### 5. ✍️ Create New Prompt Versions
**Location:** Prompt Lab Tab → "+ New Prompt Version" button

**How to Test:**
1. Open Prompt Lab tab
2. Select an agent (e.g., ORCHESTRATOR)
3. Click "+ New Prompt Version" button (top right)
4. **Expected:** Modal appears with:
   - Agent selector dropdown (should default to selected agent)
   - Prompt Text textarea
   - Notes input field
   - "Activate immediately" checkbox
   - "💾 Save Version" button

5. Fill in:
   - Prompt Text: "You are a test agent. Your job is to..."
   - Notes: "Added test instructions"
   - Check "Activate immediately" if desired

6. Click "💾 Save Version"
7. **Expected:** 
   - Success alert shows with version ID
   - Modal closes
   - New version appears in version pills (v4, v5, etc.)
   - If activated, becomes the "Active" version

**What Changed:**
- Before: Modal existed but had no form handler - button clicks did nothing
- After: Complete form submission flow, creates new prompt version via POST `/prompts/`

---

### 6. 🎯 Create New A/B Tests
**Location:** A/B Testing Tab → "+ New A/B Test" button

**How to Test:**
1. Open A/B Testing tab
2. Click "+ New A/B Test" button (top right)
3. **Expected:** Modal appears with:
   - Test Name input
   - Segment dropdown (Premium, HNI, Standard)
   - Channel dropdown (WhatsApp, Email, Voice, All)
   - Split ratio dropdown (50/50, 70/30, 80/20)
   - Variant A text textarea
   - Variant B text textarea
   - Success Metric dropdown
   - "🚀 Launch A/B Test" button

4. Fill in test form:
   - Test Name: "Premium Tagline Test"
   - Segment: "Premium"
   - Channel: "WhatsApp"
   - Split: "50/50"
   - Variant A: "Your family's protection matters. Renew today."
   - Variant B: "₹12,500 today = ₹50L cover. Don't leave family unprotected."
   - Metric: "Renewal Conversion Rate"

5. Click "🚀 Launch A/B Test"
6. **Expected:**
   - Success alert appears with test ID
   - Modal closes
   - Test appears in "Active Tests" section on A/B Testing tab
   - Test shows both variants with initial 0% conversion (updating as policies process)

**What Changed:**
- Before: Modal existed but had no form handler - button clicks did nothing, only hardcoded example test visible
- After: Complete form submission flow, creates new A/B test via POST `/dashboard/abtests`

---

## Troubleshooting

### If channel distribution shows "Loading channel data..."
- Check browser console for errors
- Verify `/dashboard/overview` endpoint is running
- Confirm it returns `channel_distribution` array

### If renewal rates show "Loading renewal data..."
- Check browser console for errors
- Verify `/dashboard/policies` endpoint is returning policies with `segment` and `status` fields
- Check that policies have valid segment values: HNI, Premium, Standard

### If filters don't work
- Open browser DevTools → Console
- Type: `typeof filterPolicies` should return "function"
- Check that both `allPolicies` array is populated (should have data after fetchPolicies() runs)
- Verify dropdown values match policy segment/status values exactly

### If prompt modal form doesn't submit
- Check DevTools → Console for JavaScript errors
- Verify form ID is `newPromptForm`
- Confirm all form field IDs exist: newPromptAgent, newPromptText, newPromptNotes, activateNow
- Check that `/prompts/` endpoint accepts POST with agent_name, prompt_text, notes, is_active

### If A/B test modal form doesn't submit
- Check DevTools → Console for JavaScript errors
- Verify form ID is `newABForm`
- Confirm all field IDs exist: abTestName, abSegment, abChannel, abSplit, abVariantA, abVariantB, abMetric
- Check that `/dashboard/abtests` endpoint exists and accepts POST requests

---

## Expected API Response Formats

### /dashboard/overview
```json
{
  "total_policies": 247,
  "active_policies": 214,
  "ai_managed": 180,
  "human_managed": 67,
  "distress_cases": 3,
  "open_escalations": 1,
  "channel_distribution": [
    {"last_channel": "Email", "count": 98},
    {"last_channel": "WhatsApp", "count": 125},
    {"last_channel": "Voice", "count": 24}
  ],
  "last_24h_actions": [...]
}
```

### /dashboard/policies
```json
{
  "policies": [
    {
      "policy_id": "POL-1234",
      "customer_name": "John Doe",
      "segment": "Premium",
      "status": "ACTIVE",
      "annual_premium": 15000,
      ...
    }
  ],
  "count": 247
}
```

### /dashboard/recent-activity
```json
{
  "activities": [
    {
      "policy_id": "POL-1234",
      "customer_name": "John Doe",
      "agent_name": "ORCHESTRATOR",
      "action_type": "Channel selected",
      "created_at": "2024-02-28T14:30:00"
    }
  ]
}
```

---

## Success Criteria ✅

All of these should work without errors:

- [ ] Channel distribution shows dynamic counts (not hardcoded 612, 341, 71)
- [ ] Renewal rates calculated correctly per segment
- [ ] Recent activity table populates with real data
- [ ] Segment and Status filters work in Policies tab
- [ ] Search filter works in Policies tab
- [ ] New Prompt Version modal opens and form submits
- [ ] New A/B Test modal opens and form submits
- [ ] No JavaScript errors in console
- [ ] All API calls use correct token authentication
- [ ] Modal close button works (Esc key optional)

---

## Performance Notes

For testing with production data:
- Initial load of Overview tab fetches from 2 endpoints: `/dashboard/overview` + `/dashboard/policies`
- Renewal rate calculation loops through all policies - may be slow with >10K records
- Consider adding a dedicated backend endpoint for segment renewal rates if performance is an issue
- All operations are client-side after initial data load - filtering is instant

---

Generated: March 2024
Status: ✅ All Hardcoded Elements Fixed - Ready for Testing
