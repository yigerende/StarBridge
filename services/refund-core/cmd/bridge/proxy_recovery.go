package main

import (
	"context"
	"llm-refund/internal/refundworker"
	"llm-refund/internal/store"
	"log"
	"time"
)

func recoverChildProxies(ctx context.Context, s *store.Store, p refundworker.ProtocolRunner) {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		job, err := s.ClaimChildProxyRecovery(ctx)
		if err != nil {
			if ctx.Err() == nil {
				log.Print("Child proxy recovery claim failed")
			}
			continue
		}
		if job == nil {
			continue
		}
		probeCtx, cancel := context.WithTimeout(ctx, 35*time.Second)
		result, err := p.ProbeProxy(probeCtx, job.URL)
		cancel()
		if ctx.Err() != nil {
			return
		} // Lease expires safely if the process is shutting down.
		if e := s.FinishChildProxyRecovery(ctx, job, err == nil && result.Valid); e != nil {
			log.Print("Child proxy recovery result could not be saved")
		}
	}
}
