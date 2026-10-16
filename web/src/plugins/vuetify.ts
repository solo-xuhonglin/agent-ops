import 'vuetify/styles'
import { aliases, mdi } from 'vuetify/iconsets/mdi'
import { createVuetify } from 'vuetify'

export default createVuetify({
  icons: { defaultSet: 'mdi', aliases, sets: { mdi } },
  theme: {
    defaultTheme: 'light',
    themes: {
      light: {
        dark: false,
        colors: {
          primary: '#4B1D7E',
          secondary: '#3B1370',
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
