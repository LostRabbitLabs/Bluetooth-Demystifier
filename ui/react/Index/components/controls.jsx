import React from 'react';
import PropTypes from 'prop-types';

import { debounce } from 'lodash';

import Grid from '@mui/material/Grid';
import Select from '@mui/material/Select';
import Checkbox from '@mui/material/Checkbox';
import MenuItem from '@mui/material/MenuItem';
import IconButton from '@mui/material/IconButton';
import InputLabel from '@mui/material/InputLabel';
import FormGroup from '@mui/material/FormGroup';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import FormControl from '@mui/material/FormControl';
import CloseIcon from '@mui/icons-material/Close';
import InputAdornment from '@mui/material/InputAdornment';
import FormControlLabel from '@mui/material/FormControlLabel';

export const GraphControls = ({
  count,
  timeframe,
  showTag,
  setShowTag,
  showName,
  setShowName,
  setFilter,
  graphHeight,
  setGraphHeight,
  plotAll,
  setPlotAll,
  handleCountChange,
  handleTimeframeChange,
  dataCount,
  showVendor,
  setShowVendor,
  loading,
}) => {
  const [filterText, setFilterText] = React.useState('');

  const debouncedSearch = React.useRef(debounce((text) => {
    setFilter(text);
  }, 600)).current;

  const applyFilter = (v) => {
    setFilterText(v);
    debouncedSearch(v);
  };

  const hourMinutesDays = timeframe >= (60 * 24)
    ? 'Day'
    : (timeframe >= 60)
      ? 'Hour'
      : 'Minute';

  const hourMinutesDaysNumber = timeframe >= (60 * 24)
    ? ((timeframe / 60) / 24)
    : (timeframe >= 60)
      ? (timeframe / 60)
      : timeframe;

  const plural = hourMinutesDaysNumber > 1 ? 's' : '';

  const title = timeframe === 'all'
    ? ''
    : `Last ${hourMinutesDaysNumber} ${hourMinutesDays}${plural}`;

  const clearFilter = (e) => {
    applyFilter('');
  };

  const ClearInputAdornment = (
    <InputAdornment position='end'>
      <IconButton
        fontSize='small'
        aria-label='clear filter'
        onClick={clearFilter}
        onMouseDown={clearFilter}
        edge='end'
      >
        <CloseIcon fontSize='small' />
      </IconButton>
    </InputAdornment>
  )

  return (
    <>
      <Grid item xs={12}>
        <Grid container spacing={2}>
          <Grid item>
            <Typography variant='h4' sx={{ display: 'inline-block' }}>
              {count === 'all' ? 'All' : `Top ${count}`}
              {' '}
              {title}
            </Typography>
          </Grid>
          <Grid item sx={{ flexGrow: 1}}>
            <Grid container spacing={2}>
              <Grid item>
                <TextField
                  variant='outlined'
                  type='number'
                  size='small'
                  label='Graph Height'
                  InputProps={{
                    inputProps: {
                      min: 50,
                      step: 50
                    }
                  }}
                  value={graphHeight}
                  onChange={(e) => setGraphHeight(Number(e.target.value))}
                  sx={{ maxWidth: 100 }}
                />
              </Grid>
              <Grid item>
                <FormControl size='small'>
                  <InputLabel id='count-label'>Count</InputLabel> 
                  <Select
                    labelId='count-label'
                    value={count}
                    label='Count'
                    size='small'
                    onChange={handleCountChange}
                    sx={{ minWidth: 80 }}
                  >
                    <MenuItem value={5}>Top 5</MenuItem>
                    <MenuItem value={10}>Top 10</MenuItem>
                    <MenuItem value={15}>Top 15</MenuItem>
                    <MenuItem value={25}>Top 25</MenuItem>
                    <MenuItem value={50}>Top 50</MenuItem>
                    <MenuItem value={100}>Top 100</MenuItem>
                    <MenuItem value={500}>Top 500</MenuItem>
                    <MenuItem value={'all'}>All</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              <Grid item>
                <FormControl size='small'>
                  <InputLabel id='timeframe-label'>Last Seen</InputLabel>
                  <Select
                    labelId='timeframe-label'
                    value={timeframe}
                    label='Last Seen'
                    onChange={handleTimeframeChange}
                    sx={{ minWidth: 100 }}
                  >
                    <MenuItem value={5}>5 Minutes</MenuItem>
                    <MenuItem value={15}>15 Minutes</MenuItem>
                    <MenuItem value={30}>30 Minutes</MenuItem>
                    <MenuItem value={60}>60 Minutes</MenuItem>
                    <MenuItem value={60 * 12}>12 Hours</MenuItem>
                    <MenuItem value={60 * 24}>24 Hours</MenuItem>
                    <MenuItem value={(60 * 24) * 2}>2 Days</MenuItem>
                    <MenuItem value={(60 * 24) * 7}>1 Week</MenuItem>
                    <MenuItem value={(60 * 24) * 30}>1 Month</MenuItem>
                    <MenuItem value={'all'}>All</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              <Grid item>
                <FormGroup size='small' row>
                  {timeframe !== 'all' && (
                    <FormControlLabel
                      label='Plot All'
                      control={
                        <Checkbox
                          checked={plotAll}
                          onChange={(e) => setPlotAll(e.target.checked)}
                        />
                      }
                    />
                  )}
                  <FormControlLabel
                    label='Show Names'
                    control={
                      <Checkbox
                        checked={showName}
                        onChange={(e) => setShowName(e.target.checked)}
                      />
                    }
                  />
                  <FormControlLabel
                    label='Show Tags'
                    control={
                      <Checkbox
                        checked={showTag}
                        onChange={(e) => setShowTag(e.target.checked)}
                      />
                    }
                  />
                  <FormControlLabel
                    label='Show Vendor'
                    control={
                      <Checkbox
                        checked={showVendor}
                        onChange={(e) => setShowVendor(e.target.checked)}
                      />
                    }
                  />
                </FormGroup>
              </Grid>
              <Grid item sx={{ display: 'flex', justifyContent: 'flex-end', flexGrow: 1 }}>
                <span
                  style={{ display: 'flex', alignItems: 'center', marginRight: 15 }}
                >
                  Unique Macs: {dataCount}
                </span>
                <TextField
                  label='Filter'
                  variant='outlined'
                  size='small'
                  sx={{ float: 'right' }}
                  value={filterText}
                  onChange={(e) => applyFilter(e.target.value)}
                  InputProps={{
                    endAdornment: ClearInputAdornment
                  }}
                />
              </Grid>
            </Grid>
            
          </Grid>
        </Grid>
        
      </Grid>
    </>
  );
};

GraphControls.propTypes = {
  count: PropTypes.oneOfType([
    PropTypes.bool,
    PropTypes.number,
    PropTypes.string
  ]),
  timeframe: PropTypes.oneOfType([
    PropTypes.bool,
    PropTypes.number,
    PropTypes.string
  ]),
  showName: PropTypes.bool,
  setShowName: PropTypes.func,
  filter: PropTypes.string,
  setFilter: PropTypes.func,
  handleCountChange: PropTypes.func,
  handleTimeframeChange: PropTypes.func,
  graphHeight: PropTypes.number,
  setGraphHeight: PropTypes.func,
  plotAll: PropTypes.bool,
  setPlotAll: PropTypes.func,
  showTag: PropTypes.bool,
  setShowTag: PropTypes.func,
  dataCount: PropTypes.number,
  showVendor: PropTypes.bool,
  setShowVendor: PropTypes.func,
  loading: PropTypes.bool
};

export default GraphControls;
