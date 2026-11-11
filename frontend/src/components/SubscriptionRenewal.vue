<script setup lang="ts">
import type {Row} from '../lib'
import {renewalNeedsConfirmation,renewalConfirmationNotice} from '../subscriptionStatus'
defineProps<{order:Row}>()
</script>
<template><div v-if="renewalNeedsConfirmation(order)" class="subscription-renewal"><span class="badge renewal-confirmation">续费待确认</span><small>{{renewalConfirmationNotice}}</small></div></template>
<style scoped>.subscription-renewal{margin-top:8px;max-width:260px}.renewal-confirmation{background:#fff5df;color:#925b12;border:1px solid #f2deb0;font-size:10px}.subscription-renewal small{display:block;line-height:1.7;font-size:11px;color:var(--muted);margin-top:6px}</style>
