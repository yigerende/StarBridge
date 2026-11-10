package refundworker

import (
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"llm-refund/internal/store"
)

var diagnosticWriters sync.WaitGroup

// A slow/unavailable diagnostics database cannot block protocol reads or
// checkpoint acknowledgements. Each attempt has a fixed memory budget.
func diagnosticQueue(refundID string, callback func(store.Checkpoint) error) (func(store.RefundDiagnostic), func()) {
	queue := make(chan store.RefundDiagnostic, 128)
	var dropped atomic.Int64
	diagnosticWriters.Add(1)
	go func() {
		defer diagnosticWriters.Done()
		ticker := time.NewTicker(100 * time.Millisecond)
		defer ticker.Stop()
		batch := make([]store.RefundDiagnostic, 0, 16)
		flush := func() {
			if len(batch) == 0 {
				return
			}
			if err := callback(store.Checkpoint{Method: "diagnostic_batch", Diagnostics: batch}); err != nil {
				// Still secret-free, bounded evidence survives in the container log if DB
				// persistence is unavailable. No exception/error string is logged.
				slog.Error("refund diagnostic batch persistence failed", "refund_id", refundID, "count", len(batch), "evidence", batch)
			}
			batch = make([]store.RefundDiagnostic, 0, 16)
		}
		for {
			select {
			case d, ok := <-queue:
				if !ok {
					flush()
					if n := dropped.Load(); n > 0 {
						slog.Warn("refund diagnostic queue dropped events", "refund_id", refundID, "count", n)
					}
					return
				}
				batch = append(batch, d)
				if len(batch) == 16 {
					flush()
				}
			case <-ticker.C:
				flush()
			}
		}
	}()
	return func(d store.RefundDiagnostic) {
		d = d.Sanitized()
		d.ObservedAt = time.Now().UTC()
		select {
		case queue <- d:
		default:
			dropped.Add(1)
		}
	}, func() { close(queue) }
}
