<script setup>
import { computed } from 'vue'

const props = defineProps({
  name: {
    type: String,
    default: 'sparkles',
  },
  size: {
    type: [Number, String],
    default: 16,
  },
  stroke: {
    type: [Number, String],
    default: 2,
  },
})

const icons = {
  sparkles: {
    lines: [
      { x1: 12, y1: 3, x2: 12, y2: 7 },
      { x1: 12, y1: 17, x2: 12, y2: 21 },
      { x1: 3, y1: 12, x2: 7, y2: 12 },
      { x1: 17, y1: 12, x2: 21, y2: 12 },
    ],
    path: ['M12 7l1.7 3.3L17 12l-3.3 1.7L12 17l-1.7-3.3L7 12l3.3-1.7z'],
  },
  database: {
    ellipse: [
      { cx: 12, cy: 5, rx: 8, ry: 3 },
      { cx: 12, cy: 12, rx: 8, ry: 3 },
      { cx: 12, cy: 19, rx: 8, ry: 3 },
    ],
    path: ['M4 5v14', 'M20 5v14'],
  },
  plug: {
    path: ['M9 7v4', 'M15 7v4', 'M6 11h12', 'M12 11v6', 'M10 17h4', 'M9 21h6'],
  },
  users: {
    circle: [
      { cx: 9, cy: 8, r: 3 },
      { cx: 17, cy: 9, r: 2.3 },
    ],
    path: ['M4 20c0-3 2.3-5 5-5h0c2.7 0 5 2 5 5', 'M14 20c0-2.3 1.8-4 4-4'],
  },
  shield: {
    path: ['M12 3l7 3v6c0 5-3 8-7 9-4-1-7-4-7-9V6z', 'M9.5 12l1.8 1.8L15 10'],
  },
  'file-text': {
    path: ['M7 3h7l5 5v13H7z', 'M14 3v5h5', 'M10 13h6', 'M10 17h6'],
  },
  qrcode: {
    path: [
      'M4 4h6v6H4z',
      'M14 4h6v6h-6z',
      'M4 14h6v6H4z',
      'M14 14h2v2h-2z',
      'M18 14h2v2h-2z',
      'M16 18h2v2h-2z',
      'M14 18h2v2h-2z',
      'M18 18h2v2h-2z',
    ],
  },
  refresh: {
    path: ['M20 6v5h-5', 'M4 18v-5h5', 'M20 11a8 8 0 10-2.3 5.7', 'M4 13a8 8 0 002.3-5.7'],
  },
  'rotate-cw': {
    path: ['M21 2v6h-6', 'M21 8a9 9 0 11-2.6-6.4'],
  },
  pulse: {
    path: ['M3 12h4l2-5 4 10 2-5h6'],
  },
  pause: {
    path: ['M8 5v14', 'M16 5v14'],
  },
  logout: {
    path: ['M15 3h4a2 2 0 012 2v14a2 2 0 01-2 2h-4', 'M10 17l5-5-5-5', 'M15 12H3'],
  },
  search: {
    circle: [{ cx: 11, cy: 11, r: 6 }],
    path: ['M20 20l-3.8-3.8'],
  },
  'check-circle': {
    circle: [{ cx: 12, cy: 12, r: 9 }],
    path: ['M8.5 12.2l2.2 2.3 4.8-4.8'],
  },
  calendar: {
    path: [
      'M4 7h16',
      'M7 3v4',
      'M17 3v4',
      'M5 5h14a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2z',
    ],
  },
  rocket: {
    path: ['M7 17l-2 4 4-2 8-8a6 6 0 00-8-8L1 11z', 'M14 10l0 0', 'M12 4l8 8'],
    circle: [{ cx: 14, cy: 10, r: 1.2 }],
  },
  eye: {
    path: ['M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z'],
    circle: [{ cx: 12, cy: 12, r: 2.5 }],
  },
  download: {
    path: ['M12 3v11', 'M8 10l4 4 4-4', 'M4 19h16'],
  },
  target: {
    circle: [
      { cx: 12, cy: 12, r: 8.5 },
      { cx: 12, cy: 12, r: 4.5 },
      { cx: 12, cy: 12, r: 1.5 },
    ],
  },
  'arrow-left': {
    path: ['M14 6l-6 6 6 6', 'M20 12H8'],
  },
  'arrow-right': {
    path: ['M10 6l6 6-6 6', 'M4 12h12'],
  },
  'file-plus': {
    path: ['M7 3h7l5 5v13H7z', 'M14 3v5h5', 'M12 12v6', 'M9 15h6'],
  },
  copy: {
    path: ['M9 9h11v12H9z', 'M4 15V4h11'],
  },
  'external-link': {
    path: ['M14 4h6v6', 'M20 4l-9 9', 'M20 13v7H4V4h7'],
  },
  x: {
    path: ['M6 6l12 12', 'M18 6L6 18'],
  },
}

const iconDef = computed(() => icons[props.name] || icons.sparkles)
</script>

<template>
  <svg
    class="app-icon"
    xmlns="http://www.w3.org/2000/svg"
    fill="none"
    :viewBox="'0 0 24 24'"
    :width="size"
    :height="size"
    stroke="currentColor"
    :stroke-width="stroke"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path v-for="d in iconDef.path || []" :key="`p-${d}`" :d="d" />
    <line
      v-for="line in iconDef.lines || []"
      :key="`l-${line.x1}-${line.y1}-${line.x2}-${line.y2}`"
      :x1="line.x1"
      :y1="line.y1"
      :x2="line.x2"
      :y2="line.y2"
    />
    <circle
      v-for="circle in iconDef.circle || []"
      :key="`c-${circle.cx}-${circle.cy}-${circle.r}`"
      :cx="circle.cx"
      :cy="circle.cy"
      :r="circle.r"
    />
    <ellipse
      v-for="ellipse in iconDef.ellipse || []"
      :key="`e-${ellipse.cx}-${ellipse.cy}-${ellipse.rx}-${ellipse.ry}`"
      :cx="ellipse.cx"
      :cy="ellipse.cy"
      :rx="ellipse.rx"
      :ry="ellipse.ry"
    />
  </svg>
</template>
