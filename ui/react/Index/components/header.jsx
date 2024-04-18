import React from 'react';
import PropTypes from 'prop-types';

import axios from 'axios';
import { parse } from 'json2csv';

import Menu from '@mui/material/Menu';
import Grid from '@mui/material/Grid';
import Switch from '@mui/material/Switch';
import Divider from '@mui/material/Divider';
import MenuItem from '@mui/material/MenuItem';
import Typography from '@mui/material/Typography';
import PauseIcon from '@mui/icons-material/Pause';
import IconButton from '@mui/material/IconButton';
import SettingsIcon from '@mui/icons-material/Settings';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import RefreshIcon from '@mui/icons-material/Refresh';
import Brightness4Icon from '@mui/icons-material/Brightness4';
import Brightness7Icon from '@mui/icons-material/Brightness7';
import FormControlLabel from '@mui/material/FormControlLabel';
import CircularProgress from '@mui/material/CircularProgress';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';

import { useTheme } from '@mui/material/styles';

import { parseDate } from '../../common/util';
import { ColorModeContext } from '../index';

export const Header = ({
  lastQuery,
  polling,
  setPolling,
  pollingEnabled,
  refreshData,
  mainLoading,
}) => {
  const theme = useTheme();
  const colorMode = React.useContext(ColorModeContext);
  const [anchor, setAnchor] = React.useState(null);
  const [adapter, setAdapter] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [exporting, setExporting] = React.useState(false);
  const [transport, setTransport] = React.useState('auto');
  const menuOpen = Boolean(anchor);

  const exportDataCSV = (d) => {
    setExporting(true);

    const time = lastQuery.get('time');
    const all = lastQuery.get('all');
    
    axios.get(`/api/query?${lastQuery.toString()}&include=rssi,data`).then((res) => {
      const { data } = res;
      const fields = ['time', 'addr', 'name', 'ouid.vendor', 'type', 'value'];
      
      const allPoints = data.map((macObject) => {
        const enrichedDataPoints = macObject.data.map((d) => ({
          ...d,
          ...macObject,
          type: 'DATA',
          value: `${d.key}: ${d.value}`,
          time: parseDate(d.time),
        }));

        const enrichedRssiPoints = macObject.rssi.map((d) => ({
          ...d,
          ...macObject,
          type: 'RSSI',
          value: d.rssi,
          time: parseDate(d.time)
        }));

        const enrichedUuidPoints = macObject.uuid.map((u) => ({
          ...macObject,
          ...u,
          type: 'UUID',
          value: u.uuid,
          time: parseDate(u.time),
        }));

        const enrichedPresencePoints = macObject.presence.filter((p) => p.type === 'seen').map((p) => ({
          ...macObject,
          ...p,
          type: 'SEEN',
          time: parseDate(p.time)
        }));

        return [
          { ...macObject, time: parseDate(macObject.first_seen), type: 'First Seen' },
          ...enrichedDataPoints,
          ...enrichedRssiPoints,
          ...enrichedUuidPoints,
          ...enrichedPresencePoints,
          { ...macObject, time: parseDate(macObject.last_seen), type: 'Last Seen' }
        ]
      });

      const inTimeframePoints = !all ? allPoints.flat().filter((p) => (p.time >= new Date(time))) : allPoints.flat();
      const chronologicalOrderPoints = inTimeframePoints.sort((f, l) => f.time - l.time).map((p) => ({
        ...p,
        time: p.time.toLocaleString()
      }));

      const csv = parse(chronologicalOrderPoints, { fields });
      const blob = new Blob([csv], { type: 'text/csv' });
      const csvURL = window.URL.createObjectURL(blob);
      const tempLink = document.createElement('a');
      tempLink.href = csvURL;
      tempLink.setAttribute('download', 'export.csv');
      tempLink.click();
      
    }).catch((err) => {
      console.error(`Here: ${err}`);
    }).finally(() => setExporting(false));
  };

  const dumpAllData = () => {
    const prompt = 'All collected data will be deleted. Are you sure you want to do this?';

    const deleteData = () => {
      return axios.delete(`/api/query`).then((res) => {
        console.log(res);

        if (res.data.success) {
          setAnchor(null);
        }
      }).catch((err) => {
        console.error(err);
      });
    }

    if (confirm(prompt)) {
      window.controller.abort();

      if (adapter.Discovering) {
        toggleScan({ target: { checked: false }}).then(deleteData);
      } else {
        deleteData();
      }
    }
  }

  const getAdapter = () => {
    window.adapterLoading = true;
    return axios.get(`/api/adapter`).then((res) => {
      setAdapter(res.data);
      setTransport(res.data.transport);
      window.adapterLoading = false;
    }).catch((err) => {
      console.error(err);
      window.adapterLoading = false;
    });
  }

  const toggleScan = (e) => {
    const onOff = e.target.checked ? 'on' : 'off';
    setLoading(true);

    return axios.get(`/api/adapter/scan/${onOff}`).then((res) => {
      setTimeout(() => {
        if (!window.adapterLoading) {
          getAdapter().then(() => {
            setLoading(false);
          }).catch((err) => {
            setLoading(false);
          });
        }
      }, 3000);
    }).catch((err) => {
      setLoading(false);
      console.error(err);
    });
  }

  const changeTransport = (e, v) => {
    axios.post('/api/adapter/transport', {
      'transport': v
    }).then((res) => {
      if (res.data.success) {
        setTransport(v);
      }
    }).catch((err) => {
      console.error(err);
    })
  };

  React.useEffect(() => {
    clearInterval(window.adapterTicker);
    getAdapter().then(() => setLoading(false));
    window.adapterTicker = setInterval(() => {
      if (!window.adapterLoading) {
        getAdapter().then(() => setLoading(false));
      }
    }, 15000);
  }, []);

  return (
    <>
      <Grid item xs={12}>
        <Grid container>
          <Grid item xs>
            <img src='/dist/img/logo.png' width={80} style={{ verticalAlign: 'middle' }} />
            <Typography variant='h3' sx={{ display: 'inline-block', verticalAlign: 'middle' }}>
              Bluetooth Demystifier
            </Typography>
          </Grid>
          <Grid item sx={{flexGrow: 1, display: 'flex', alignItems: 'center', justifyContent: 'flex-end'}}>
            <Menu
              anchorEl={anchor}
              open={menuOpen}
              onClose={() => setAnchor(null)}
              anchorOrigin={{
                vertical: 'bottom',
                horizontal: 'left',
              }}
              transformOrigin={{
                vertical: 'top',
                horizontal: 'right',
              }}
            >
              <MenuItem
                disabled={exporting}
                onClick={() => exportDataCSV()}
              >
                Export Data (csv)
              </MenuItem>
              <MenuItem onClick={dumpAllData}>Dump Data</MenuItem>
            </Menu>

            {mainLoading && (
              <CircularProgress
                size={40}
                sx={{
                  position: 'relative',
                  left: '40px',
                }}
              />
            )}

            {pollingEnabled ? (
              <IconButton onClick={() => setPolling(!polling)} sx={{ marginRight: '10px' }}>
                {polling ? <PauseIcon /> : <PlayArrowIcon />}
              </IconButton>
            ) : (
              <IconButton onClick={refreshData} sx={{ marginRight: '10px' }} disabled={mainLoading}>
                <RefreshIcon />
              </IconButton>
            )}

            <FormControlLabel
              label={adapter.Discovering ? 'Scanning' : 'Scan Off'}
              control={
                <Switch
                  disabled={loading}
                  checked={adapter.Discovering || false}
                  onChange={toggleScan}
                />
              }
            />

            <ToggleButtonGroup
              size='small'
              color='primary'
              value={transport}
              exclusive
              onChange={changeTransport}
              sx={{ '> button': { width: '70px'} }}
            >
              <ToggleButton value='bredr'>BR/EDR</ToggleButton>
              <ToggleButton value='auto'>Auto</ToggleButton>
              <ToggleButton value='le'>LE</ToggleButton>
            </ToggleButtonGroup>

            <IconButton onClick={colorMode.toggleColorMode}>
              {theme.palette.mode === 'dark' ? <Brightness4Icon /> : <Brightness7Icon />}
            </IconButton>
            <IconButton onClick={(e) => setAnchor(e.currentTarget)}>
              <SettingsIcon />
            </IconButton>
          </Grid>
        </Grid>
        
        <Divider flexItem style={{ margin: '10px 0' }} />
      </Grid>
    </>
  );
};

Header.propTypes = {
  lastQuery: PropTypes.oneOfType([
    PropTypes.object,
    PropTypes.string,
  ]),
  polling: PropTypes.bool,
  setPolling: PropTypes.func,
  pollingEnabled: PropTypes.bool,
  refreshData: PropTypes.func,
  mainLoading: PropTypes.bool,
};

export default Header;
