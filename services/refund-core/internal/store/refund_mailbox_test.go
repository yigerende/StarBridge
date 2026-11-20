package store

import (
	"bytes"
	"encoding/json"
	"errors"
	"llm-refund/internal/refundprovider"
	"testing"
)

func TestOptionalMailboxKeepsLegacyHashAndRejectsInvalidLinks(t *testing.T) {
	a := account("mailbox@test.invalid")
	raw, _ := json.Marshal([]refundprovider.Account{a})
	if string(raw) != `[{"email":"mailbox@test.invalid","password":"offline-fixture-password","totp_secret":"JBSWY3DPEHPK3PXP"}]` {
		t.Fatal("Existing request hash changed")
	}
	for _, value := range []string{"", "https://mail.example.test/get-code?uid=private", "https://mail.example.test/m#private"} {
		a.EmailCodeURL = value
		if err := ValidateAccounts([]refundprovider.Account{a}); err != nil {
			t.Fatal(err)
		}
	}
	for _, value := range []string{"file:///etc/passwd", "http://", "http://user:pass@example.test", "https://example.test/\nheader"} {
		a.EmailCodeURL = value
		if err := ValidateAccounts([]refundprovider.Account{a}); err == nil {
			t.Fatal("Invalid URL accepted")
		}
	}
}

func TestMailboxEncryptedReplayedClaimedAndNeverPublic(t *testing.T) {
	s, a, u := fixture(t)
	configure(t, s, a, 1)
	acct := account("mailbox@test.invalid")
	acct.EmailCodeURL = "https://mail.example.test/get-code?uid=private-fixture"
	ids, replay, err := s.CreateRefunds(testctx, u, "optional-mailbox", []refundprovider.Account{acct}, 1000)
	if err != nil || replay {
		t.Fatal(err)
	}
	again, replay, err := s.CreateRefunds(testctx, u, "optional-mailbox", []refundprovider.Account{acct}, 1000)
	if err != nil || !replay || again[0] != ids[0] {
		t.Fatal("Replay lost", err)
	}
	var encrypted []byte
	if err = s.Pool.QueryRow(testctx, "SELECT credentials FROM refunds WHERE id=$1", ids[0]).Scan(&encrypted); err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(encrypted, []byte("private-fixture")) {
		t.Fatal("Mailbox secret stored as plaintext")
	}
	saved, err := s.Credentials(testctx, u, ids[0])
	if err != nil || saved.EmailCodeURL != acct.EmailCodeURL {
		t.Fatal("Mailbox not decrypted", err)
	}
	j, err := s.ClaimRefund(testctx)
	if err != nil || j == nil || j.Account.EmailCodeURL != acct.EmailCodeURL {
		t.Fatal("Mailbox not passed to executor", err)
	}
	row, err := s.Refund(testctx, u, ids[0])
	if err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(row)
	if bytes.Contains(data, []byte("private-fixture")) || bytes.Contains(data, []byte("email_code_url")) {
		t.Fatal("Secret returned to customer")
	}
	acct.EmailCodeURL = "https://mail.example.test/another"
	if _, _, err = s.CreateRefunds(testctx, u, "optional-mailbox", []refundprovider.Account{acct}, 1000); !errors.Is(err, ErrConflict) {
		t.Fatal("Changed URL did not conflict", err)
	}
	balance(t, s, u, 10000, 1000)
}
