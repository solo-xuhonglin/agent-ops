import 'vuetify/styles'
import { aliases, mdi } from 'vuetify/iconsets/mdi'
import { createVuetify } from 'vuetify'

export default createVuetify({
  icons: { defaultSet: 'mdi', aliases, sets: { mdi } },
  theme: {
    defaultTheme: 'light',
    themes: {
      light: {
        colors: {
          primary: '#8B2FC9',
          secondary: '#C03FD8',
        },
      },
    },
  },
})
