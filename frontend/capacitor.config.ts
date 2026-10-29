import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.naspilot.englishflow',
  appName: 'EnglishFlow',
  webDir: 'dist',
  server: {
    androidScheme: 'http',
  },
};

export default config;