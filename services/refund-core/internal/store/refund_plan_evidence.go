package store

// Private account-matching facts; credentials and complete upstream bodies are
// never accepted. Keep missing/null/type information for failed comparisons.
type RefundPlanEvidence struct {
	Phase       string            `json:"phase"`
	Result      string            `json:"result"`
	Reason      string            `json:"reason,omitempty"`
	WaitSeconds int               `json:"wait_seconds"`
	ElapsedMS   int               `json:"elapsed_ms"`
	Fields      map[string]string `json:"fields,omitempty"`
	Types       map[string]string `json:"types,omitempty"`
	Checks      map[string]bool   `json:"checks,omitempty"`
	AccountKeys []string          `json:"account_keys,omitempty"`
	Truncated   bool              `json:"truncated,omitempty"`
}

const planIDFields = "token.account_id session.account_id expected.team_id expected.personal_id selected_key account.account_id default.account_id"
const planLabelFields = "token.plan_type session.plan_type session.structure account.structure account.plan_type default.structure default.plan_type entitlement.subscription_plan entitlement.plan_type"
const planBoolFields = "session.is_deactivated account.is_deactivated default.is_deactivated entitlement.has_active_subscription"
const planReasons = "session_email_mismatch session_account_mismatch session_user_mismatch live_email_mismatch invalid_response missing_accounts account_missing personal_account_mismatch invalid_entitlement invalid_plan unknown_plan invalid_subscription_state conflicting_plan missing_plan conflicting_subscription_state query_failed query_error"

func (v RefundPlanEvidence) sanitized() *RefundPlanEvidence {
	v.Phase = evidenceEnum(v.Phase, "wait session me accounts")
	v.Result = evidenceEnum(v.Result, "waiting observed free paid rejected")
	if v.Phase == "" || v.Result == "" {
		return nil
	}
	v.Reason = evidenceEnum(v.Reason, planReasons)
	v.WaitSeconds = evidenceNumber(v.WaitSeconds, 60)
	v.ElapsedMS = evidenceNumber(v.ElapsedMS, 900000)
	fields, types, checks := make(map[string]string), make(map[string]string), make(map[string]bool)
	for k, value := range v.Fields {
		if (evidenceEnum(k, planIDFields) != "" && workspaceIDPattern.MatchString(value)) ||
			(evidenceEnum(k, planLabelFields) != "" && workspacePlanPattern.MatchString(value)) {
			fields[k] = value
		}
	}
	for k, value := range v.Types {
		if evidenceEnum(k, planIDFields+" "+planLabelFields+" "+planBoolFields+" response accounts item account default entitlement") != "" {
			if typ := evidenceEnum(value, "missing null boolean string object array number other"); typ != "" {
				types[k] = typ
			}
		}
	}
	for k, value := range v.Checks {
		if evidenceEnum(k, planBoolFields+" session_account_matches child_id_matches live_email_matches account_id_matches personal_structure") != "" {
			checks[k] = value
		}
	}
	keys := make([]string, 0, 16)
	for _, key := range v.AccountKeys {
		if len(keys) == 16 {
			v.Truncated = true
			break
		}
		if workspaceIDPattern.MatchString(key) {
			keys = append(keys, key)
		}
	}
	v.Fields, v.Types, v.Checks, v.AccountKeys = fields, types, checks, keys
	return &v
}
