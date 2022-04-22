import { hot } from 'react-hot-loader/root';
import React from 'react';
import ReactDom from 'react-dom';
import PropTypes from 'prop-types';

import axios from 'axios';

import { DateTime } from 'luxon';

import Box from '@mui/material/Box';
import Grid from '@mui/material/Grid';
import Container from '@mui/material/Container';
import { ThemeProvider, createTheme } from '@mui/material/styles';

import { theme } from '../common/theme';
import { Header } from './components/header';
import { Graph } from './components/graphs';
import { 
  uuidFilter,
  parseDate
} from '../common/util';

import './main.scss';

export const ColorModeContext = React.createContext({ toggleColorMode: () => {} });

export const Index = () => {
  window.controller = new AbortController();
  const preferedColorScheme = localStorage.getItem('colorScheme') || 'light';
  const [mode, setMode] = React.useState(preferedColorScheme);
  const [loading, setLoading] = React.useState(false);
  const [polling, setPolling] = React.useState(true);
  const [pollingEnabled, setPollingEnabled] = React.useState(true);
  const [filter, setFilter] = React.useState('');
  const [graphHeight, setGraphHeight] = React.useState(850);
  const [selectedMac, setSelectedMac] = React.useState(false);
  const [timeframe, setTimeframe] = React.useState(5);
  const [plotAll, setPlotAll] = React.useState(false);
  const [count, setCount] = React.useState(5);
  const [data, setData] = React.useState([]);
  const [lastQuery, setLastQuery] = React.useState('');

  const colorMode = React.useMemo(() => ({
    toggleColorMode: () => {
      setMode((prevMode) => {
        const newMode = (prevMode === 'light' ? 'dark' : 'light');
        localStorage.setItem('colorScheme', newMode);
        return newMode;
      });
    },
  }), []);

  const fetchData = (timeframe) => {
    window.loading = true;
    setLoading(true);
    const params = new URLSearchParams();

    if (timeframe && timeframe !== 'all') {
      const newTime = DateTime.now().minus({ minutes: timeframe });
      const timestamp = new Date(newTime);
      params.append('time', timestamp.toJSON());
    }

    if (count && count !== 'all') {
      params.append('count', count);
    }
    
    if (plotAll) {
      params.append('all', true);
    }

    let route = `/api/query/sample?${params.toString()}`;

    setLastQuery(params);

    const promise = axios.get(route, {
      signal: window.controller.signal
    });

    promise.then((res) => {
      if (window.controller.signal.aborted) {
        return;
      }
      
      if (res.data) {  
        setData(res.data);
      }

      setLoading(false);
      window.loading = false;
    }).catch((err) => {
      console.error('Fetching Presence Data Failed');
      window.loading = false;
      setLoading(false);
    });
  };

  const getMacData = React.useRef((mac, cb = () => {}) => {
    window.macController = new AbortController();
    window.macLoading = true;
    setLoading(true);
    axios.get(`/api/query/${mac}`, { signal: window.macController.signal })
      .then((res) => {
        if (window.macController.signal.aborted) {
          return;
        }

        if (res.data) {
          setSelectedMac(res.data[0]);
        }

        setLoading(false);
        window.macLoading = false;
      }).catch((err) => {
        console.error(err);
        window.macLoading = false;
      }).finally(cb);
  }).current;

  /**
   * selectMac
   * polls the api for the selected mac to display
   * in the MacInteraction card.
   * 
   * @param {object} v data plot 
   */
  const selectMac = React.useRef((v, cb) => {
    try {
      window.macController.abort();
    } catch(err) {}
    
    clearInterval(window.macTicker);
    
    if (v) {
      getMacData(v.y, cb);
      window.polling = polling;
      window.macTicker = setInterval(() => {
        if (window.macLoading) {
          console.log('skip request. currently loading');
        }

        if (!window.polling) {
          console.log('skip request. polling off');
        }

        if (!window.macLoading && window.polling) {
          getMacData(v.y, cb);
        }
      }, 5000);
    } else {
      setSelectedMac(false);
    }
  }).current;

  React.useEffect(() => {
    window.polling = polling;
    window.controller.abort();
    clearInterval(window.ticker);
    fetchData(timeframe);

    const enablePolling = timeframe <= 60;
    setPollingEnabled(enablePolling);

    if (!enablePolling) {
      window.polling = false;
    }

    window.ticker = setInterval(() => {
      window.controller = new AbortController();
      if (!window.loading && window.polling) {
        fetchData(timeframe)
      }
    }, 5000);
  }, [timeframe, count, polling, plotAll]);

  const themeOverride = React.useMemo(() => 
    createTheme({
      ...theme,
      palette: {
        mode
      }
    }),
    [mode]);

  /**
   * filteredMacs
   * 
   * uses the filter to compare if we should graph the mac or not
   */
  const filteredMacs = data.filter((m) => (
    m.addr.indexOf(filter) > -1
    || m.addr.toLowerCase().indexOf(filter) > -1
    || m.addr.replace(/:/g, '').toLowerCase().indexOf(filter) > -1
    || m.name.indexOf(filter) > -1
    || m.name.toLowerCase().indexOf(filter) > -1
    || m.tag.indexOf(filter) > -1
    || m.tag.toLowerCase().indexOf(filter) > -1
    || m.ouid?.vendor?.toLowerCase().indexOf(filter) > -1
    || m.ouid?.category?.icon?.indexOf(filter) > -1
    || m.ouid?.category?.name?.toLowerCase().indexOf(filter) > -1
    || m.ouid?.category?.description?.toLowerCase().indexOf(filter) > -1
    || uuidFilter(filter, m)
  ));

  /**
   * MacTimestampData
   *
   * For each FILTERED Mac Address, returns all timestamps for all
   * data points seen and format as { x: timestamp, y: ADDR }
   */
  const MacTimestampData = filteredMacs.map((mac) => {
    const y = mac.addr;
    const color = mac.ouid?.category?.color;

    // add timestamps from 'data' objects
    const timestamps = mac.data.map((data) => ({
      type: 'data',
      time: parseDate(data.time),
      key: data.key,
      value: data.value
    }));
    // add timestamps from 'rssi' objects
    timestamps.push(mac.rssi.map((rssi) => ({
      type: 'rssi',
      time: parseDate(rssi.time),
      rssi: rssi.rssi
    })));

    // add presence timestamps
    timestamps.push(mac.presence.map((presence) => ({
      type: presence.type,
      time: parseDate(presence.time)
    })));

    // push last_seen since NEW emits update this variable
    timestamps.push({
      type: 'last seen',
      time: parseDate(mac.last_seen)
    });

    const values = timestamps.flat().map((ts) => {
      const object = { x: ts.time, y, type: ts.type };

      if (color) {
        object.color = color;
      } else {
        object.color = 'rgb(18, 147, 154)'; // default color hardcoded.
      }

      if (ts.type === 'data' && ts.key && ts.value) {
        object['key'] = ts.key;
        object['value'] = ts.value;
      } else if (ts.type === 'rssi' && ts.rssi) {
        object['rssi'] = ts.rssi;
      } else {
        object['seen'] = ts.time;
      }

      return object;
    });

    return values.flat();
  });

  return (
    <ColorModeContext.Provider value={colorMode}>
      <ThemeProvider theme={themeOverride}>
        <Box sx={{
          position: 'fixed',
          top: 0,
          bottom: 0,
          left: 0,
          right: 0,
          bgcolor: 'background.default',
          zIndex: '-1'
        }}/>
        <Container maxWidth={false} sx={{ color: 'text.primary' }}>
          <Grid container spacing={3}>
            <Header
              lastQuery={lastQuery}
              polling={polling}
              setPolling={setPolling}
              pollingEnabled={pollingEnabled}
              refreshData={() => fetchData(timeframe)}
              mainLoading={loading}
            />
            <Graph
              data={data}
              MacTimestampData={MacTimestampData}
              numMacs={filteredMacs.length}
              count={count}
              setCount={setCount}
              timeframe={timeframe}
              setTimeframe={setTimeframe}
              selectMac={selectMac}
              selectedMac={selectedMac}
              filter={filter}
              setFilter={setFilter}
              graphHeight={graphHeight}
              setGraphHeight={setGraphHeight}
              plotAll={plotAll}
              setPlotAll={setPlotAll}
              loading={loading}
            />
          </Grid>
        </Container>
      </ThemeProvider>
    </ColorModeContext.Provider>
  );
};

ReactDom.render(<Index />, document.querySelector('#root'));

export default hot(Index);
