package refundprovider

type Account struct {
	Email        string `json:"email"`
	Password     string `json:"password"`
	TOTPSecret   string `json:"totp_secret"`
	EmailCodeURL string `json:"email_code_url,omitempty"`
}
