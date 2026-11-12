<script setup lang="ts">
import FinanceReports from './FinanceReports.vue'
import type {Row} from '../lib'
defineProps<{user:Row;pub:Row}>()
const emit=defineEmits<{refresh:[];expired:[];navigate:[path:string]}>()
</script>
<template><FinanceReports :user="user" :pub="pub" @refresh="emit('refresh')" @expired="emit('expired')" @navigate="emit('navigate',$event)"/></template>
