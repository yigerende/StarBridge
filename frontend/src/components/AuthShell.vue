<script setup lang="ts">
import {onMounted,onUnmounted,ref} from 'vue'
import {ArrowUpRight,Bell,Pause,Play,Sparkles,Layers,RotateCcw} from 'lucide-vue-next'
import AuthForm from './AuthForm.vue'
import AnimatedCount from './AnimatedCount.vue'
import {usePublicCounts} from '../publicCounts'
import type {Row} from '../lib'
defineProps<{pub:Row}>()
defineEmits<{authenticated:[]}>()
const {counts,unavailable}=usePublicCounts()
const motion=ref(!matchMedia('(prefers-reduced-motion: reduce)').matches),visible=ref(!document.hidden)
const preference=matchMedia('(prefers-reduced-motion: reduce)')
function preferenceChanged(){motion.value=!preference.matches}
function visibilityChanged(){visible.value=!document.hidden}
onMounted(()=>{preference.addEventListener('change',preferenceChanged);document.addEventListener('visibilitychange',visibilityChanged)})
onUnmounted(()=>{preference.removeEventListener('change',preferenceChanged);document.removeEventListener('visibilitychange',visibilityChanged)})
const services=[{key:'registration',label:'注册订单',icon:Sparkles},{key:'subscription',label:'订阅订单',icon:Layers},{key:'refund',label:'代退订单',icon:RotateCcw}] as const
</script>
<template>
 <main class="auth-scene" :class="{'motion-paused':!motion||!visible}">
  <section class="auth-showcase" aria-label="平台订单统计">
   <div class="auth-grid" aria-hidden="true"></div><div class="auth-glow" aria-hidden="true"></div>
   <header class="auth-brand-row"><a href="#/home" class="auth-brand"><img :src="pub.site.icon_url||'/site-icon'" alt=""/><span>{{pub.site.name}}<small>{{pub.site.english_name}}</small></span></a><button class="auth-motion" type="button" :aria-label="motion?'暂停动态效果':'开启动态效果'" :title="motion?'暂停动态效果':'开启动态效果'" :aria-pressed="motion" @click="motion=!motion"><Pause v-if="motion" :size="14"/><Play v-else :size="14"/></button></header>
   <div class="auth-intro"><p class="auth-kicker"><i></i> 每一笔，都有迹可循</p><h1>连接所需<span>即刻启程<span class="auth-period">.</span></span></h1><p class="auth-description">注册、订阅、代退。<br/>从下单到交付，在星桥一站办理。</p></div>
   <div class="auth-constellation" aria-hidden="true">
    <div class="auth-orbit-field"><div class="auth-ring ring-one"></div><div class="auth-ring ring-two"></div><div class="auth-ring ring-three"></div><div class="auth-orbit-traveler"><i></i></div><div class="auth-orbit-traveler second"><i></i></div></div>
    <div class="auth-core"><svg viewBox="0 0 100 100" fill="none"><path d="M27 67 50 25l23 42M36 52h28M23 73h54" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><path d="m50 16 2 7 7 2-7 2-2 7-2-7-7-2 7-2z" fill="currentColor" stroke="none"/></svg></div>
    <span class="auth-orbit-tag tag-registration"><Sparkles :size="13"/>注册</span><span class="auth-orbit-tag tag-subscription"><Layers :size="13"/>订阅</span><span class="auth-orbit-tag tag-refund"><RotateCcw :size="13"/>代退</span>
    <i class="auth-star star-one"></i><i class="auth-star star-two"></i><i class="auth-star star-three"></i>
   </div>
   <div class="auth-volume"><div class="auth-volume-label"><span>累计订单</span><span class="auth-volume-note">{{unavailable?'统计暂不可用':'与每一位用户，共同积累'}}<ArrowUpRight :size="14"/></span></div><div class="auth-volume-total"><AnimatedCount :value="unavailable?null:counts?.total" :animate="motion&&visible"/><small>笔</small></div><div class="auth-volume-grid"><div v-for="service in services" :key="service.key"><span><component :is="service.icon" :size="13"/>{{service.label}}</span><strong><AnimatedCount :value="unavailable?null:counts?.[service.key]" :animate="motion&&visible"/><small>笔</small></strong></div></div></div>
   <footer class="auth-showcase-foot"><span>{{pub.site.english_name}}<i></i> YOUR NEXT CONNECTION</span><span>注册 · 订阅 · 代退</span></footer>
  </section>
  <section class="auth-entry" aria-label="登录与注册">
   <header class="auth-entry-top"><span>一个账户，连接全部服务</span><span class="auth-language">简体中文 <span>ZH</span></span></header>
   <div class="auth-form-wrap"><AuthForm :pub="pub" @authenticated="$emit('authenticated')"/></div>
   <div v-if="pub.announcements?.length" class="auth-notice"><Bell :size="16"/><div><strong>{{pub.announcements[0].title}}</strong><p>{{pub.announcements[0].body}}</p></div></div>
   <footer class="auth-entry-foot"><span>© {{new Date().getFullYear()}} {{pub.site.english_name}}</span><a v-if="pub.features?.docs!==false" href="/docs" target="_blank" rel="noopener noreferrer">接口文档<ArrowUpRight :size="13"/></a></footer>
  </section>
 </main>
</template>
<style scoped src="../auth-scene.css"></style>
