import 'vuetify/styles'
import { aliases, mdi } from 'vuetify/iconsets/mdi'
import { createVuetify } from 'vuetify'

export default createVuetify({
  icons: { defaultSet: 'mdi', aliases, sets: { mdi } },
  defaults: {
    VAppBar: { flat: true, height: 52 },
    VList: { density: 'compact' },
    VTextField: { density: 'comfortable' },
    VBtn: { flat: true, height: 40 },
  },
  theme: {
    defaultTheme: 'light',
    themes: {
      light: {
        dark: false,
        colors: {
          primary: '#4B1D7E',
          background: '#FFFFFF',
          surface: '#FFFFFF',
          'surface-variant': '#F5F6F8',
        },
        variables: {
          'border-color': '#E7E7EC',
          'border-opacity': 1,
        },
      },
    },
  },
})
