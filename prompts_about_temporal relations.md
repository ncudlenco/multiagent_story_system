2. TEMPORAL STRUCTURE (CRITICAL - ACTOR ACTION CHAINS REQUIRED):
   "temporal": {{
       "starting_actions": {{"actor1": "first_event_id", "actor2": "first_event_id"}},
       "event_id": {{"relations": ["relation_id"], "next": "same_actor_next_event_id_or_null"}},
       "relation_id": {{"type": "after|before|starts_with", "source": "event1", "target": "event2"}}
   }}

   REQUIRED STRUCTURE:
   - starting_actions is a FLAT object (actor_id -> event_id), NOT nested
   - Event entries have ONLY "relations" and "next" fields
   - Relation entries have "type", "source", "target" fields

   CRITICAL RULES FOR ACTOR ACTION CHAINS:

   Rule A - EVERY ACTOR MUST HAVE A COMPLETE ACTION CHAIN:
     * starting_actions MUST map EVERY actor to their first event ID
     * Each actor's events MUST be connected via "next" pointers
     * Events cannot be orphaned - must be reachable from starting_actions
     * The chain ends when "next": null (final action)
     * NEVER leave "next" undefined - must be event_id OR null

     Example - 2 actors with 2 actions each:
     "starting_actions": {{"actor1": "e1", "actor2": "e3"}}
     "e1": {{"relations": [], "next": "e2"}},  // actor1's first action
     "e2": {{"relations": [], "next": null}},  // actor1's last action (next=null)
     "e3": {{"relations": [], "next": "e4"}},  // actor2's first action
     "e4": {{"relations": ["r1"], "next": null}}  // actor2's last action (next=null)

   Rule B - CROSS-ACTOR RELATIONS USE "relations" FIELD:
     * Use "before"/"after"/"concurrent" for events of DIFFERENT actors
     * Add relation ID to BOTH events' "relations" arrays
     * NEVER use "next" to connect events of different actors
     * "next" is ONLY for same-actor sequential actions

     Example - actor1's e2 happens before actor2's e3:
     "e2": {{"relations": ["r1"], "next": null}},  // actor1's last action
     "r1": {{"type": "before", "source": "e2", "target": "e3"}},  // cross-actor relation
     "e3": {{"relations": ["r1"], "next": "e4"}}  // actor2's first action

   Rule C - Exist Events Are Part of Action Chains:
     * Exist events MUST be included in starting_actions
     * Exist events MUST have "next" pointing to first action (or null if no actions)

     Example:
     "starting_actions": {{"writer": "writer"}},  // Exist event is the start
     "writer": {{"relations": [], "next": "sit_and_type"}},  // Exist → first action
     "sit_and_type": {{"relations": [], "next": null}}  // Action → end

   Rule D - Parent Scenes Have No Temporal Relations:
     * Parent scenes do NOT appear in temporal dictionary
     * Only LEAF scenes (action events) have temporal entries
     * Parent scenes use semantic/logical relations only

   COMPLETE EXAMPLE - 2 Actors with Full Action Chains:

   {{
     "temporal": {{
       "starting_actions": {{
         "writer": "writer",      // Exist event for writer
         "observer": "observer"   // Exist event for observer
       }},
       "writer": {{
         "relations": [],
         "next": "sit_and_type"   // ← Exist event points to first action
       }},
       "sit_and_type": {{
         "relations": ["r1"],
         "next": null             // ← Last action in writer's chain
       }},
       "observer": {{
         "relations": [],
         "next": "watch_writer"   // ← Exist event points to first action
       }},
       "watch_writer": {{
         "relations": ["r1"],
         "next": null             // ← Last action in observer's chain
       }},
       "r1": {{
         "type": "concurrent",    // Cross-actor relation
         "source": "sit_and_type",
         "target": "watch_writer"
       }}
     }}
   }}

   EXPLANATION OF THIS EXAMPLE:
   1. starting_actions maps BOTH actors to their Exist events
   2. Writer's chain: writer → sit_and_type → null
   3. Observer's chain: observer → watch_writer → null
   4. "next" connects same-actor actions (writer→sit_and_type, observer→watch_writer)
   5. "relations" + r1 connects cross-actor events (sit_and_type concurrent with watch_writer)
   6. Every event has a "next" field (either event_id or null)
   7. Both actors are in starting_actions

   COMMON MISTAKES TO AVOID:
   ✗ Missing starting_actions entry for an actor
   ✗ Event without "next" field (must be present, even if null)
   ✗ Using "next" to connect different actors' events (use "relations" instead)
   ✗ Exist event not in starting_actions
   ✗ Broken chain (event A's "next" points to event B, but event B doesn't exist)




YOUR TAKS:
   3. Build COMPLETE temporal chains for EVERY actor:
   - starting_actions MUST include ALL actors mapped to their Exist events
   - EVERY event MUST have "next" field (event_id or null)
   - Connect same-actor actions with "next" pointers
   - Connect cross-actor relations with "relations" field + relation IDs
   - Verify: Can you trace from starting_actions through "next" to reach every event?


VALIDATION CHECKLIST (verify before returning your output):
✓ Every actor in starting_actions?
✓ Every event has "next" field (not missing)?
✓ Actor chains complete (can trace from starting_actions → next → next → null for each actor)?
✓ No "next" pointing to different actor's events?
✓ Cross-actor relations use "relations" field + relation IDs (not "next")?
✓ Exist event IDs match entity names ("writer": {{"Entities": ["writer"]}})?
✓ All actions are from valid action list?
✓ All locations are from valid episode list?
