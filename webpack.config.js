const path = require('path');
const webpack = require('webpack');

module.exports = {
  entry: {
    index: [
      'react-hot-loader/patch',
      path.join(__dirname, 'ui/react/Index/index.jsx')
    ],
  },
  output: {
    path: path.join(__dirname, 'ui/templates/dist/js'),
    filename: '[name].bundle.js',
  },
  module: {
    rules: [
      {
        test: /\.jsx$/,
        exclude: /node_modules/,
        use: {
          loader: 'babel-loader',
          options: {
            presets: ['@babel/preset-env']
          }
        },
      },
      {
        test: /\.s[ac]ss$/i,
        exclude: /node_modules/,
        use: [
          'style-loader',
          'css-loader',
          'sass-loader',
        ]
      },
      {
        test: /\.css$/i,
        use: [
          'style-loader',
          'css-loader',
        ]
      }
    ]
  },
  plugins: [],
  resolve: {
    extensions: ['.js', '.jsx', '.sass', '.css'],
    alias: {
      react: path.join(__dirname, 'node_modules/react')
    }
  },
  devServer: {
    static: {
      directory: path.join(__dirname, 'ui/templates/'),
      watch: true,
    },
    devMiddleware: {
      writeToDisk: true,
    },
    historyApiFallback: true,
    port: 9001,
    hot: true,
    proxy: {
      '/api': {
        target: 'http://localhost:1338/',
        secure: false,
      }
    }
  }
};
