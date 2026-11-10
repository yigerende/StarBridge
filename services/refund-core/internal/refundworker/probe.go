package refundworker

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net"
	"os"
	"os/exec"
	"time"

	"llm-refund/internal/store"
)

type MotherProbeResult = store.ResourceCheck

func probeMessage(code string) string {
	switch code {
	case "unauthorized":
		return "凭据已失效，请更新母号凭据"
	case "forbidden":
		return "访问被拒绝，请检查账号权限或代理出口"
	case "proxy_auth":
		return "代理认证失败，请检查代理用户名和密码"
	case "rate_limited":
		return "请求过于频繁，请稍后重试"
	case "tls_config":
		return "服务器证书配置异常，请联系维护人员"
	case "tls_verify":
		return "安全连接证书校验失败，请检查代理出口"
	case "dns":
		return "代理或目标地址解析失败，请检查网络配置"
	case "timeout":
		return "连接超时，请检查代理后重试"
	case "connect":
		return "无法连接代理或目标服务，请检查代理配置"
	case "identity":
		return "母号身份与团队不匹配，请检查凭据和团队 ID"
	case "missing":
		return "请先填写代理地址"
	case "shape":
		return "返回数据暂时无法识别，请稍后重试"
	case "http":
		return "服务响应异常，请稍后重试"
	case "network":
		return "网络连接失败，请检查代理后重试"
	default:
		return "检查暂未完成，请稍后重试或联系维护人员"
	}
}
func safeProbeCode(code string) string {
	switch code {
	case "ok", "unauthorized", "forbidden", "proxy_auth", "rate_limited", "tls_config", "tls_verify", "dns", "timeout", "connect", "identity", "missing", "shape", "http", "network":
		return code
	default:
		return "runtime"
	}
}
func safeSeat(v *store.SeatCount) *store.SeatCount {
	if v == nil {
		return nil
	}
	for _, n := range []int{v.Total, v.Remaining, v.Used, v.Held} {
		if n < 0 || n > 1000000 {
			return nil
		}
	}
	if v.Remaining > v.Total {
		return nil
	}
	return v
}

type boundedProbeOutput struct{ data bytes.Buffer }

func (b *boundedProbeOutput) Write(p []byte) (int, error) {
	if b.data.Len()+len(p) > 4096 {
		return 0, io.ErrShortBuffer
	}
	return b.data.Write(p)
}

func (p ProtocolRunner) probe(ctx context.Context, mode string, job any) (store.ResourceCheck, error) {
	started := time.Now()
	result := store.ResourceCheck{Code: "runtime", CheckedAt: started.UTC()}
	raw, err := json.Marshal(job)
	if err != nil {
		return result, err
	}
	cmd := exec.CommandContext(ctx, p.Python, "-u", p.Script, mode)
	cmd.Env = append(os.Environ(), "PYTHONUTF8=1", "PYTHONIOENCODING=utf-8")
	cmd.Stdin, cmd.Stderr = bytes.NewReader(raw), io.Discard
	var output boundedProbeOutput
	cmd.Stdout = &output
	err = cmd.Run()
	var wire store.ResourceCheck
	if err == nil && json.Unmarshal(output.data.Bytes(), &wire) == nil {
		// Never forward process prose, headers or unknown properties.
		result.Code = safeProbeCode(wire.Code)
		if wire.HTTPStatus >= 100 && wire.HTTPStatus <= 599 {
			result.HTTPStatus = wire.HTTPStatus
		}
		result.Valid = wire.Valid && result.Code == "ok" && result.HTTPStatus >= 200 && result.HTTPStatus < 300
		if result.Valid && mode == "--probe-proxy" {
			ip := net.ParseIP(wire.IPAddress)
			if ip == nil {
				result.Valid = false
				result.Code = "shape"
			} else {
				result.IPAddress = ip.String()
			}
		}
		if result.Valid && mode == "--probe-mother" {
			if wire.Seats != nil && wire.CapacityCode == "" {
				seats := &store.SeatCapacity{Standard: safeSeat(wire.Seats.Standard), Premium: safeSeat(wire.Seats.Premium)}
				if seats.Standard != nil || seats.Premium != nil {
					result.Seats = seats
				}
			}
			if result.Seats == nil {
				result.CapacityCode = safeProbeCode(wire.CapacityCode)
				result.CapacityMessage = "席位未读取：" + probeMessage(result.CapacityCode)
			}
		}
	} else if ctx.Err() != nil {
		result.Code = "timeout"
	}
	result.LatencyMS = time.Since(started).Milliseconds()
	result.Message = probeMessage(result.Code)
	if result.Valid {
		result.Message = "母号凭据和团队访问校验通过"
		if mode == "--probe-proxy" {
			result.Message = "代理连接正常"
		}
	}
	return result, nil
}

func (p ProtocolRunner) ProbeMother(ctx context.Context, j *store.RefundJob) (MotherProbeResult, error) {
	return p.probe(ctx, "--probe-mother", j)
}
func (p ProtocolRunner) ProbeProxy(ctx context.Context, proxy string) (store.ResourceCheck, error) {
	return p.probe(ctx, "--probe-proxy", map[string]string{"proxy": proxy})
}
