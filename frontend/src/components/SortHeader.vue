<script setup lang="ts">
import {ArrowDown,ArrowUp,ArrowUpDown} from 'lucide-vue-next'
const props=defineProps<{field:string;label:string;sortBy?:string;sortDir?:string;disabled?:boolean}>()
defineEmits<{sort:[field:string]}>()
</script>
<template><th :aria-sort="sortBy===field?(sortDir==='asc'?'ascending':'descending'):'none'"><button v-if="!disabled" type="button" class="list-sort-heading" :aria-label="`${label}，${sortBy===field&&sortDir==='asc'?'降序':'升序'}排序`" @click="$emit('sort',field)">{{label}}<ArrowUp v-if="sortBy===field&&sortDir==='asc'" :size="13"/><ArrowDown v-else-if="sortBy===field" :size="13"/><ArrowUpDown v-else :size="13"/></button><template v-else>{{label}}</template></th></template>
<style>.list-sort-heading{display:inline-flex;align-items:center;gap:6px;padding:0;border:0;background:none;color:inherit;font:inherit;text-align:left;cursor:pointer;white-space:nowrap}.list-sort-heading svg{flex:none;opacity:.65}.list-sort-heading:hover,.list-sort-heading:focus-visible{color:var(--green,#245442)}th[aria-sort=ascending] .list-sort-heading,th[aria-sort=descending] .list-sort-heading{color:var(--green,#245442)}</style>
